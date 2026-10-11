"""Causal joint registration study with independent confirmation and checkpoints.

This module does not import the Top15 or existing-account intervention controller.
Private caches contain plaintext and never belong in public/published artifacts.
"""
from __future__ import annotations
from collections import Counter, OrderedDict
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import random
import time

from ai.pcfg_monte_carlo import Grammar, MonteCarloIndex
from ai.pcfg_adapter import PCFGAttacker, PCFGConfig
from ai.dual_attack import omen_prefix, omen_identity
from core.corpus import counts_hash
from core.uniformity import concentration, forecast_collision
from experiments.registration_joint_data import (save_private, load_private, stream, developer_stream)
from policy.registration_joint import (JointRule, Preferences, rebuild, candidates, structure, stable_seed)

VERSION = 'registration-joint-study-v1'
ARMS = ['base', 'fixed', 'refresh', 'strings', 'structures', 'joint', 'strength', 'no_recent', 'no_frozen']


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def cost_failures(summary, cfg):
    failures = []
    prefix = 'incremental_' if cfg.get('cost_mode') == 'incremental_base' else ''
    for metric, limit in [('modification_rate', cfg['max_modification']),
                          ('mean_extra_submissions', cfg['max_extra']),
                          ('more_than_three_fraction', cfg['max_more_than_three'])]:
        if summary[prefix + metric] > limit:
            failures.append(metric)
    if summary['pending_users']:
        failures.append('pending_users')
    return failures


def attach_base_cost(result, baseline):
    n = result['summary']['initial_users']
    differences = [max(0, a-b) for a, b in zip(result['attempts'], baseline['attempts'])]
    result['summary'].update(
        incremental_modification_rate=sum(a != b for a,b in zip(result['users'],baseline['users']))/n,
        incremental_mean_extra_submissions=sum(differences)/n,
        incremental_more_than_three_fraction=sum(x>3 for x in differences)/n,
        common_base_modification_rate=baseline['summary']['modification_rate'],
        common_base_mean_extra_submissions=baseline['summary']['mean_extra_submissions'])


def risk(index, counts, budgets, total=None):
    total = sum(counts.values()) if total is None else total
    hit = {b: 0 for b in budgets}
    cross = {b: 0 for b in budgets}
    outside = low = 0
    for word, n in counts.items():
        d = index.query(word)
        outside += n * (d['status'] == 'outside_model_support')
        low += n * (d['status'] == 'low_sample_support')
        rank = d['guess_count']
        for b in budgets:
            if rank is not None:
                hit[b] += n * (rank <= b)
                lo, hi = d['rank_interval95']
                cross[b] += n * (lo <= b < hi)
    return {'rates': {str(b): hit[b]/total if total else 0 for b in budgets},
            'conditional_completed_rates': {str(b): hit[b]/sum(counts.values()) if counts else 0 for b in budgets},
            'hits': {str(b): hit[b] for b in budgets}, 'initial_users': total,
            'completed_users': sum(counts.values()), 'outside_model_support_fraction': outside/total if total else 0,
            'low_sample_support_fraction': low/total if total else 0,
            'rank_interval_crossing_fraction': {str(b): cross[b]/total if total else 0 for b in budgets},
            'budget_unit': 'estimated_distinct_string_probability_rank',
            'samples': index.n, 'seed': index.seed, 'estimated': True,
            'uncertainty_note': 'pointwise Hoeffding rank intervals; not simultaneous trajectory confidence'}


class AttackCache:
    def __init__(self, directory, cfg, log):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = self.root/'pcfg_runtime'
        self.cfg, self.log = cfg, log
        self.memory = OrderedDict()
        self.fits = self.cache_hits = 0

    def grammar(self, counts):
        key = counts_hash(counts)
        path = self.root/'grammars'/(key + '.pkl.gz')
        if path.exists():
            self.cache_hits += 1
            return Grammar(**load_private(path)), key
        self.log(f'PCFG fit: {sum(counts.values()):,} training occurrences / {len(counts):,} strings')
        grammar = Grammar.fit(counts, runtime_root=self.runtime, timeout=self.cfg['attack_timeout'])
        save_private(path, grammar.payload())
        self.fits += 1
        return grammar, key

    def index(self, counts, samples, purpose='selection'):
        key = counts_hash(counts)
        seed = stable_seed(self.cfg['mc_seed'], purpose) % 2**32
        signature = (key, samples, seed)
        if signature in self.memory:
            self.memory.move_to_end(signature)
            return self.memory[signature]
        path = self.root/'indices'/f'{key}-{samples}-{seed}.pkl.gz'
        if path.exists():
            value = load_private(path)
            g = Grammar(**value['grammar'])
            index = MonteCarloIndex(g, samples, seed, sampled_counts=value['counts'], sampled_probabilities=value['probabilities'])
            self.cache_hits += 1
        else:
            grammar, _ = self.grammar(counts)
            started = time.time()
            index = MonteCarloIndex(grammar, samples, seed)
            save_private(path, {'grammar': grammar.payload(), 'counts': index.sampled_counts,
                                'probabilities': {w: grammar.probability(w) for w in index.sampled_counts}})
            self.log(f'PCFG MC: {samples:,} samples ready ({time.time()-started:.1f}s)')
        self.memory[signature] = index
        while len(self.memory) > 2:
            self.memory.popitem(last=False)
        return index

    def raw(self, counts, budget, model):
        key = counts_hash(counts)
        path = self.root/'prefixes'/f'{key}-{model}-{budget}.pkl.gz'
        if path.exists():
            return load_private(path)
        self.log(f'{model} actual prefix: {budget:,} charged attempts')
        if model == 'pcfg':
            cfg = PCFGConfig.workspace_default(timeout_seconds=self.cfg['attack_timeout'], generation_limit=budget)
            adapter = PCFGAttacker(replace(cfg, runtime_root=self.runtime))
            rows = [w for w, n in sorted(counts.items()) for _ in range(n)]
            raw = adapter.generate_raw_stream(rows, limit=budget)
            words = raw.pop('stream')
            metadata = raw
        elif model == 'omen':
            import tempfile
            directory = Path(tempfile.mkdtemp(prefix='omen-', dir=self.root))
            words, metadata = omen_prefix(counts, {'order': 3, 'wsl_distribution': 'Ubuntu-20.04',
                                                  'timeout_seconds': self.cfg['attack_timeout']}, budget, directory)
            metadata['identity'] = omen_identity()
        else:
            raise ValueError(model)
        ranks = {}
        for i, word in enumerate(words, 1):
            ranks.setdefault(word, i)
        result = {'ranks': ranks, 'raw_emissions': len(words), 'metadata': metadata,
                  'requested_budget': budget, 'complete': len(words) == budget,
                  'budget_unit': 'raw_attempts_including_duplicates'}
        save_private(path, result)
        return result


def observed(prefix, counts, budgets, total):
    points = []
    for b in budgets:
        hits = sum(n for w, n in counts.items() if prefix['ranks'].get(w, b+1) <= b)
        points.append({'budget': b, 'hits': hits, 'initial_users': total,
                       'rate_per_initial_user': hits/total if total else 0,
                       'rate_per_completed_user': hits/sum(counts.values()) if counts else 0,
                       'charged_attempts': min(b, prefix['raw_emissions']),
                       'prefix_reached_requested_budget': prefix['raw_emissions'] >= b,
                       'estimated': False})
    return {'points': points, 'metadata': prefix['metadata'], 'raw_emissions': prefix['raw_emissions'],
            'budget_unit': 'actual_attempts_including_duplicates'}


def preview_records(dev, recent_initial, seed, t, phase, n, alpha):
    rng = random.Random(stable_seed(seed, t, phase, 'initial-preview'))
    recent = [w for batch in recent_initial for _, w in batch]
    output = []
    for i in range(n):
        source = recent if recent and rng.random() < alpha else [None]
        word = rng.choice(source) if source is recent else dev[rng.randrange(len(dev))][1]
        output.append((f'preview-{phase}-{t}-{i}', word))
    return output


def reweighted_train(train_chunk, train_role, recent_initial, seed, t, alpha):
    """Use target structure proportions, never copy their raw strings into training."""
    if not recent_initial or alpha == 0:
        return train_chunk
    own = Counter(structure(w) for _, w in train_role)
    observed = Counter(structure(w) for batch in recent_initial for _, w in batch)
    own_total, observed_total = sum(own.values()), sum(observed.values())
    weights = [(1-alpha) + alpha*(observed.get(structure(w), 0)/observed_total)/(own[structure(w)]/own_total)
               for _, w in train_role]
    rng = random.Random(stable_seed(seed, t, 'structure-only-training'))
    return [(f'train-weight-{t}-{i}-{identifier}', word) for i, (identifier, word) in
            enumerate(rng.choices(train_role, weights=weights, k=len(train_chunk)))]


def select_rule(base, hold, history, recent_counts, recent_initial, dev_history, next_train,
                train_role, preview_select, preview_confirm, response, attacks, frozen_prefix,
                cfg, seed, t, arm, cohort_size, audit_path, log):
    ks = [0] if arm == 'structures' else cfg['ks']
    js = [0] if arm == 'strings' else cfg['js']
    alpha = 0 if arm == 'no_recent' else cfg['recent_weight']
    training = reweighted_train(next_train, train_role, recent_initial, seed, t, alpha)
    previews = {phase: preview_records(pool, recent_initial, seed, t, phase, cfg['preview_users'], alpha)
                for phase, pool in [('select', preview_select), ('confirm', preview_confirm)]}
    proposals = candidates(base, recent_counts, hold, ks, js)
    baseline_previews = {phase: response.simulate(records, base, seed, patience=cfg.get('patience'))
                         for phase, records in previews.items()}
    rows = []
    private = []
    # All candidates receive exactly the same users and random response sequences.
    for rule, is_hold in proposals:
        p = response.simulate(previews['select'], rule, seed, patience=cfg.get('patience'))
        attach_base_cost(p, baseline_previews['select'])
        failures = cost_failures(p['summary'], cfg)
        row = {'rule': rule.summary(), 'hold': is_hold, 'cost': p['summary'], 'failures': failures,
               'collision_forecast': forecast_collision(history, p['counts'], cohort_size)}
        rows.append(row)
        private.append({'rule': rule, 'preview': p, 'row': row})
    # No expensive attack fit can rescue a rule that already fails user costs.
    if all(x['row']['failures'] for x in private):
        write_json(audit_path, {'status': 'all_candidates_cost_infeasible_base_fallback',
                               'candidates': rows, 'confirmation': None})
        log(f'{arm} batch {t+1}: constraint conflict, common base fallback')
        return base, {'status': 'constraint_conflict_base_fallback', 'candidates': len(rows),
                      'model_fits': 0, 'updated': base.signature() != hold.signature()}
    for x in private:
        # Hold must be scored even if its costs fail, to audit reference protections.
        if x['row']['failures'] and not x['row']['hold']:
            continue
        transformed = response.simulate(training, x['rule'], seed, patience=cfg.get('patience'))
        x['row']['developer_simulated_exits'] = transformed['summary']['pending_users'] if cfg.get('patience') else 0
        if transformed['summary']['pending_users'] and not cfg.get('patience'):
            x['row']['failures'].append('developer_training_pending')
            continue
        counts = dev_history + transformed['counts']
        index = attacks.index(counts, cfg['selection_samples'], 'selection')
        h = risk(index, history, [cfg['objective_budget']])
        p = risk(index, x['preview']['counts'], [cfg['objective_budget']], cfg['preview_users'])
        n = sum(history.values())
        predicted = (n*h['rates'][str(cfg['objective_budget'])] + cohort_size*p['rates'][str(cfg['objective_budget'])])/(n+cohort_size)
        frozen = sum(v for w, v in x['preview']['counts'].items() if w in frozen_prefix['ranks'])/cfg['preview_users']
        x['train_counts'] = counts
        outside = (n*h['outside_model_support_fraction']+cohort_size*p['outside_model_support_fraction'])/(n+cohort_size)
        x['row'].update(predicted_A1=predicted, predicted_outside=outside,
                        frozen_rate=frozen, history_MC=h, preview_MC=p, training_sha256=counts_hash(counts),
                        training_role='train only; structure reweighting; no preview or target plaintext')
    reference = private[0]
    if 'predicted_A1' not in reference['row']:
        write_json(audit_path, {'status': 'hold_training_pending_base_fallback', 'candidates': rows})
        return base, {'status': 'hold_training_pending_base_fallback', 'updated': base.signature() != hold.signature()}
    for x in private:
        row = x['row']
        if 'predicted_A1' not in row:
            continue
        if arm != 'no_frozen' and row['frozen_rate'] - reference['row']['frozen_rate'] > cfg['frozen_increase']:
            row['failures'].append('frozen_risk_increase')
        if row['predicted_outside'] - reference['row']['predicted_outside'] > cfg['outside_increase']:
            row['failures'].append('outside_support_increase')
    feasible = [x for x in private if not x['row']['failures'] and 'predicted_A1' in x['row']]
    if not feasible:
        write_json(audit_path, {'status': 'all_candidates_infeasible_base_fallback', 'candidates': rows})
        return base, {'status': 'constraint_conflict_base_fallback', 'updated': base.signature() != hold.signature()}
    best = min(feasible, key=lambda x: (x['row']['predicted_A1'], x['row']['collision_forecast'],
               x['row']['cost']['mean_extra_submissions'], len(x['rule'].exact)+len(x['rule'].shapes), not x['row']['hold']))
    gain = reference['row']['predicted_A1'] - best['row']['predicted_A1']
    confirmation = None
    chosen, status = hold, 'hold_insufficient_selection_gain'
    if not best['row']['hold'] and gain >= cfg['min_gain']:
        confirmed = []
        for x in (reference, best):
            p = response.simulate(previews['confirm'], x['rule'], seed, patience=cfg.get('patience'))
            attach_base_cost(p, baseline_previews['confirm'])
            index = attacks.index(x['train_counts'], cfg['confirmation_samples'], 'confirmation')
            h = risk(index, history, [cfg['objective_budget']])
            pr = risk(index, p['counts'], [cfg['objective_budget']], cfg['preview_users'])
            n = sum(history.values())
            confirmed.append({'cost': p['summary'], 'failures': cost_failures(p['summary'], cfg),
                              'A1': (n*h['rates'][str(cfg['objective_budget'])]+cohort_size*pr['rates'][str(cfg['objective_budget'])])/(n+cohort_size),
                              'outside': (n*h['outside_model_support_fraction']+cohort_size*pr['outside_model_support_fraction'])/(n+cohort_size),
                              'frozen': sum(v for w, v in p['counts'].items() if w in frozen_prefix['ranks'])/cfg['preview_users'],
                              'history_MC': h, 'preview_MC': pr})
        ref, win = confirmed
        failures = list(win['failures'])
        if arm != 'no_frozen' and win['frozen']-ref['frozen'] > cfg['frozen_increase']:
            failures.append('confirmation_frozen_risk_increase')
        if win['outside']-ref['outside'] > cfg['outside_increase']:
            failures.append('confirmation_outside_support_increase')
        if ref['A1']-win['A1'] < cfg['min_gain']:
            failures.append('confirmation_insufficient_gain')
        confirmation = {'reference': ref, 'winner': win, 'failures': failures,
                        'single_winner_only': True, 'disjoint_preview_roles': True}
        if not failures:
            chosen, status = best['rule'], 'confirmed_update'
        else:
            status = 'hold_confirmation_failed'
    elif cost_failures(reference['preview']['summary'], cfg):
        chosen, status = base, 'infeasible_hold_no_confirmed_replacement_base_fallback'
    write_json(audit_path, {'status': status, 'candidates': rows, 'selection_gain': gain,
                           'confirmation': confirmation, 'chosen': chosen.summary()})
    log(f'{arm} batch {t+1}: {status}; selection gain {100*gain:.3f} pp')
    return chosen, {'status': status, 'updated': chosen.signature() != hold.signature(),
                    'selection_gain': gain, 'candidates': len(rows)}


def summed_cost(summaries):
    n = sum(s['initial_users'] for s in summaries)
    result = {'initial_users': n, 'completed_users': sum(s['completed_users'] for s in summaries),
              'pending_users': sum(s['pending_users'] for s in summaries)}
    for field in ['modification_rate', 'mean_extra_submissions', 'more_than_three_fraction', 'mean_edit_distance',
                  'incremental_modification_rate', 'incremental_mean_extra_submissions', 'incremental_more_than_three_fraction',
                  'common_base_modification_rate', 'common_base_mean_extra_submissions']:
        result[field] = sum((s[field] or 0)*s['initial_users'] for s in summaries)/n if n else 0
    result['completion_rate'] = result['completed_users']/n if n else 0
    return result


def trajectory(arm, cohorts, dev_cohorts, data, cfg, seed, parameters, root, attacks, frozen,
               gate_index, log, *, fixed_pair=None, gate_threshold=None):
    root = Path(root)/arm
    root.mkdir(parents=True, exist_ok=True)
    finished = root/'result.json'
    if finished.exists():
        return json.loads(finished.read_text('utf-8'))
    train_role = data['roles']['train']
    train_words = [w for _, w in train_role]
    common = frozenset(w for w, _ in sorted(Counter(train_words).items(), key=lambda p: (-p[1], p[0]))[:cfg['common_size']])
    base = JointRule(cfg['minimum'], common)
    response = Preferences(train_words, cfg['minimum'], cfg['weights'], cfg['response_mode'], cfg['candidate_limit'])
    checkpoint = root/'state.pkl.gz'
    if checkpoint.exists():
        state = load_private(checkpoint)
        # Frozen index reconstructed rather than persisted in every rule checkpoint.
        if state['rule'].strength:
            state['rule'] = replace(state['rule'], index=gate_index)
    else:
        state = {'rule': base, 'history': Counter(), 'dev_history': Counter(), 'final_batches': [],
                 'dev_batches': [], 'timeline': [], 'costs': [], 'decisions': [], 'attempts': [], 't': 0,
                 'developer_pending': 0}
    for t in range(state['t'], len(cohorts)):
        rule = state['rule']
        if t:
            width = t if cfg['window'] == 'all' else cfg['window']
            recent = sum(state['final_batches'][-width:], Counter())
            recent_initial = cohorts[max(0,t-width):t]
            if arm.startswith('cal_fixed_') or arm == 'fixed':
                if t == 1:
                    k, j = fixed_pair if fixed_pair is not None else parameters['fixed_pair']
                    rule = rebuild(base, recent, k, j)
            elif arm == 'refresh':
                rule = rebuild(base, recent, *parameters['fixed_pair'])
            elif arm.startswith('cal_gate_') or arm == 'strength':
                threshold = gate_threshold if gate_threshold is not None else parameters['strength_threshold']
                rule = replace(base, strength=threshold, index=gate_index)
            elif arm in ['strings', 'structures', 'joint', 'no_recent', 'no_frozen']:
                if state['developer_pending'] and not cfg.get('patience'):
                    rule, decision = base, {'status': 'developer_pending_base_fallback', 'updated': base.signature() != rule.signature()}
                else:
                    rule, decision = select_rule(base, rule, state['history'], recent, recent_initial,
                        state['dev_history'], train_role[t*(len(train_role)//len(cohorts)):(t+1)*(len(train_role)//len(cohorts))], train_role,
                        data['roles']['preview_select'], data['roles']['preview_confirm'], response,
                        attacks, frozen, cfg, seed, t, arm, len(cohorts[t]), root/f'candidate_audit_{t+1:02}.json', log)
                state['decisions'].append({'batch': t+1, **decision})
        start = time.time()
        actual = response.simulate(cohorts[t], rule, seed, distances=True, patience=cfg.get('patience'))
        baseline = response.simulate(cohorts[t], base, seed, patience=cfg.get('patience'))
        attach_base_cost(actual, baseline)
        developer = response.simulate(dev_cohorts[t], rule, seed, patience=cfg.get('patience'))
        if developer['summary']['pending_users']:
            # All target cohorts still run. No incomplete training role is fitted.
            write_json(root/'failure.json', {'stage': 'training_response', 'batch': t+1,
                       'pending': developer['summary']['pending_users'], 'target_summary': actual['summary']})
            state['developer_pending'] += developer['summary']['pending_users']
        state['history'].update(actual['counts'])
        state['dev_history'].update(developer['counts'])
        state['final_batches'].append(actual['counts'])
        state['dev_batches'].append(developer['counts'])
        state['costs'].append(actual['summary'])
        state['attempts'].extend(actual['attempts'])
        state['timeline'].append(rule.summary())
        state['rule'], state['t'] = rule, t+1
        # Avoid duplicating the potentially large gate index in checkpoint pickles.
        clean = dict(state)
        clean['rule'] = replace(rule, index=None)
        save_private(checkpoint, clean)
        write_json(root/'progress.json', {'arm': arm, 'completed_batches': t+1, 'total_batches': len(cohorts),
                   'users': sum(state['history'].values()), 'last_cost': actual['summary'], 'last_rule': rule.summary()})
        log(f'{arm}: batch {t+1}/{len(cohorts)}; {sum(state["history"].values()):,} completed; {time.time()-start:.1f}s')
    users = sum(len(c) for c in cohorts)
    if state['developer_pending'] and not cfg.get('patience'):
        result = {'version': VERSION, 'arm': arm, 'status': 'registrations_complete_attack_unavailable_developer_pending',
                  'seed': seed, 'timeline': state['timeline'], 'decisions': state['decisions'],
                  'cost': summed_cost(state['costs']), 'cohort_costs': state['costs'],
                  'distribution': concentration(state['history']), 'developer_pending': state['developer_pending'],
                  'A1_MC': None, 'F_MC': risk(gate_index, state['history'], cfg['budgets'], users),
                  'F_actual_small': observed(frozen, state['history'], [cfg['frozen_budget']], users),
                  'eligibility_failures': ['developer_training_pending'],
                  'attack_failures': {'A1': 'Incomplete training role retained; no silent deletion or fit'}, 'actual_attacks': {}}
        result['eligibility_failures'] += cost_failures(result['cost'], cfg)
        write_json(finished, result)
        return result
    index = attacks.index(state['dev_history'], cfg['final_samples'], 'final')
    result = {'version': VERSION, 'arm': arm, 'status': 'complete', 'seed': seed,
              'timeline': state['timeline'], 'decisions': state['decisions'],
              'cost': summed_cost(state['costs']), 'cohort_costs': state['costs'],
              'distribution': concentration(state['history']),
              'A1_training_sha256': counts_hash(state['dev_history']),
              'A1_training_occurrences': sum(state['dev_history'].values()),
              'developer_simulated_exits': state['developer_pending'] if cfg.get('patience') else 0,
              'training_exit_handling': 'only completed simulated developer accounts have passwords; exits counted explicitly' if cfg.get('patience') else 'no dropped pending developer records',
              'A1_MC': risk(index, state['history'], cfg['budgets'], users),
              'F_MC': risk(gate_index, state['history'], cfg['budgets'], users),
              'F_actual_small': observed(frozen, state['history'], [cfg['frozen_budget']], users),
              'cohort_A1_MC': [], 'actual_attacks': {}, 'attack_failures': {}}
    result['cost']['p90_extra_submissions'] = sorted(state['attempts'])[min(users-1, int(.9*users))]
    result['eligibility_failures'] = cost_failures(result['cost'], cfg)
    result['cost']['policy_updates'] = sum(a != b for a, b in zip(state['timeline'], state['timeline'][1:]))
    if cfg.get('cohort_aware', False) and not arm.startswith('cal_'):
        for targets, training in zip(state['final_batches'], state['dev_batches']):
            ci = attacks.index(training, cfg['final_samples'], 'final')
            result['cohort_A1_MC'].append(risk(ci, targets, cfg['budgets'], users//len(cohorts)))
    if cfg.get('actual_attacks', False) and not arm.startswith('cal_'):
        prefixes = {}
        for model in ['pcfg', 'omen']:
            try:
                prefix = attacks.raw(state['dev_history'], max(cfg['budgets']), model)
                prefixes[model] = prefix
                result['actual_attacks'][model] = observed(prefix, state['history'], cfg['budgets'], users)
            except Exception as exc:
                result['attack_failures'][model] = f'{type(exc).__name__}: {exc}'
                log(f'{arm}: {model} failed; explicit missing result: {exc}')
        result['F_actual_attacks'] = {}
        frozen_train = Counter(train_words)
        for model in ['pcfg', 'omen']:
            try:
                prefix = attacks.raw(frozen_train, max(cfg['budgets']), model)
                result['F_actual_attacks'][model] = observed(prefix, state['history'], cfg['budgets'], users)
            except Exception as exc:
                result['attack_failures']['F_'+model] = f'{type(exc).__name__}: {exc}'
        if len(prefixes) == 2:
            for kind, divisor in [('union_each_model_B', 1), ('union_equal_total_B', 2)]:
                points = []
                for b in cfg['budgets']:
                    per = b//divisor
                    hits = sum(n for w, n in state['history'].items() if any(p['ranks'].get(w, per+1) <= per for p in prefixes.values()))
                    points.append({'budget': b, 'per_model_budget': per, 'max_total_attempts': per*2,
                                   'hits': hits, 'initial_users': users, 'rate_per_initial_user': hits/users})
                result['actual_attacks'][kind] = {'points': points, 'estimated': False}
    if result['attack_failures']:
        result['status'] = 'registrations_complete_with_missing_attack_results'
    write_json(finished, result)
    return result


def run_job(request):
    cfg, task, study_root = request['cfg'], request['task'], Path(request['root'])
    data = load_private(study_root/'private'/'partition.pkl.gz')
    scenario, seed, phase = task['scenario'], task['seed'], task['phase']
    variant = task.get('variant', 'main')
    root = study_root/'runs'/variant/phase/scenario/str(seed)
    root.mkdir(parents=True, exist_ok=True)
    log_file = root/'events.log'
    def log(message):
        with log_file.open('a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + message + '\n')
        print(f'[{variant}/{phase}/{scenario}/{seed}] {message}', flush=True)
    try:
        # Per-job backend prevents concurrent training-cache mutation.
        attacks = AttackCache(study_root/'private'/'attacks'/scenario/str(seed), cfg, log)
        train = Counter(w for _, w in data['roles']['train'])
        frozen = attacks.raw(train, cfg['frozen_budget'], 'pcfg')
        gate = attacks.index(train, cfg['final_samples'], 'frozen')
        role = 'validation' if phase != 'test' else 'test'
        cohorts = stream(data['roles'][role], scenario, seed, cfg['users'], cfg['batches'], data['median_length'])
        dev = developer_stream(data['roles']['train'], scenario, seed, cfg['batches'], data['median_length'])
        params_path = study_root/'frozen_parameters'/f'{scenario}.json'
        parameters = json.loads(params_path.read_text('utf-8')) if params_path.exists() else {}
        results = {}
        if phase == 'calibration':
            # With a common preference sequence and a narrower acceptance set,
            # retries cannot decrease and an initially rejected base word cannot
            # become initially accepted. If base already fails an absolute cost
            # limit, no narrowing rule can pass it with all users completed.
            base_result = trajectory('cal_fixed_0_0', cohorts, dev, data, cfg, seed, parameters, root, attacks, frozen, gate, log,
                                     fixed_pair=(0,0))
            results['cal_fixed_0_0'] = base_result
            impossible = cfg.get('cost_mode') != 'incremental_base' and bool(base_result['eligibility_failures'])
            def screened(arm):
                value = {'version':VERSION, 'arm':arm, 'status':'screened_cost_ineligible_by_common_base_lower_bound',
                         'seed':seed, 'cost':base_result['cost'], 'cost_is_lower_bound':True,
                         'eligibility_failures':list(base_result['eligibility_failures']), 'A1_MC':None,
                         'proof':'same preferences + acceptance subset; extra attempts and required modifications cannot decrease; pending not eligible',
                         'registrations_executed_for_this_arm':False}
                write_json(root/arm/'result.json',value)
                return value
            for k in cfg['ks']:
                for j in cfg['js']:
                    arm = f'cal_fixed_{k}_{j}'
                    if arm == 'cal_fixed_0_0':
                        continue
                    results[arm] = screened(arm) if impossible else trajectory(arm, cohorts, dev, data, cfg, seed, parameters, root, attacks, frozen, gate, log,
                                                                               fixed_pair=(k,j))
            for threshold in cfg['strength_thresholds']:
                arm = f'cal_gate_{threshold}'
                results[arm] = screened(arm) if impossible else trajectory(arm, cohorts, dev, data, cfg, seed, parameters, root, attacks, frozen, gate, log,
                                                                          gate_threshold=threshold)
        else:
            if not parameters:
                raise ValueError('Comparison parameters must be frozen from validation before execution')
            for arm in cfg.get('arms', ARMS):
                results[arm] = trajectory(arm, cohorts, dev, data, cfg, seed, parameters, root, attacks, frozen, gate, log)
        summary = {'task': task, 'status': 'complete', 'arms': results,
                   'new_grammar_fits_this_invocation': attacks.fits, 'cache_hits_this_invocation': attacks.cache_hits}
        write_json(root/'summary.json', summary)
        return {'task': task, 'status': 'complete', 'summary_path': str(root/'summary.json')}
    except Exception as exc:
        import traceback
        write_json(root/'job_failure.json', {'task': task, 'error': str(exc), 'traceback': traceback.format_exc()})
        log(f'JOB FAILED: {type(exc).__name__}: {exc}')
        raise
