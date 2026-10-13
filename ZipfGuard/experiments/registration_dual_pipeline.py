"""Actual PCFG + OMEN controller; separate from the frozen PCFG-MC study.

Every candidate and confirmation uses B raw attempts per model. Confirmation
reuses deterministic attack prefixes but uses a separate response-preview pool.
"""
from collections import Counter, OrderedDict
from dataclasses import replace
from pathlib import Path
import json
import time

from core.corpus import counts_hash
from core.uniformity import concentration, forecast_collision
from policy.registration_joint import JointRule, Preferences, rebuild, candidates
from experiments.registration_joint_data import save_private, load_private
from experiments.registration_joint_pipeline import (
    AttackCache, write_json, cost_failures, attach_base_cost, observed,
    preview_records, reweighted_train, summed_cost)

VERSION = 'registration-actual-dual-v1'
MODELS = ('pcfg', 'omen')


class ActualDualIndex:
    def __init__(self, grammar, prefixes, budget):
        if set(prefixes) != set(MODELS):
            raise ValueError('Both PCFG and OMEN are required')
        for model, prefix in prefixes.items():
            if not prefix['complete'] or prefix['raw_emissions'] < budget:
                raise ValueError(f'Incomplete {model} prefix: no zero-risk substitution')
        self.grammar, self.prefixes, self.budget = grammar, prefixes, budget
        self.meta = prefixes['omen']['metadata']
        self.alphabet = frozenset(self.meta['alphabet'])
        self.union_ranks = dict(prefixes['pcfg']['ranks'])
        for word, rank in prefixes['omen']['ranks'].items():
            self.union_ranks[word] = min(rank, self.union_ranks.get(word, rank))

    def outside(self, word):
        omen_support = (self.meta['min_length'] <= len(word) <= self.meta['max_length']
                        and all(c in self.alphabet for c in word))
        return not omen_support and self.grammar.probability(word) <= 0


class DualAttackCache(AttackCache):
    def index(self, counts, samples=None, purpose='selection'):
        # The retained argument supports the inherited trajectory interface;
        # no Monte Carlo samples or PCFG-only screening are used.
        budget = self.cfg['objective_budget'] if purpose in ('selection', 'confirmation') else max(self.cfg['budgets'])
        signature = (counts_hash(counts), budget)
        if signature in self.memory:
            self.memory.move_to_end(signature)
            return self.memory[signature]
        prefixes = {m: self.raw(counts, budget, m) for m in MODELS}
        grammar, _ = self.grammar(counts)
        index = ActualDualIndex(grammar, prefixes, budget)
        self.memory[signature] = index
        while len(self.memory) > 2:
            self.memory.popitem(last=False)
        self.log(f'Dual actual index ready: {budget:,} attempts EACH; purpose={purpose}')
        return index


def risk(index, counts, budgets, total=None):
    if max(budgets) > index.budget:
        raise ValueError('Risk budget exceeds actual prefixes')
    total = sum(counts.values()) if total is None else total
    hits = {str(b): sum(n for w, n in counts.items()
                       if index.union_ranks.get(w, b+1) <= b) for b in budgets}
    outside = sum(n for w, n in counts.items() if index.outside(w))
    return {'rates': {b: n/total if total else 0 for b, n in hits.items()},
            'hits': hits, 'initial_users': total, 'completed_users': sum(counts.values()),
            'outside_model_support_fraction': outside/total if total else 0,
            'outside_both_models_weight': outside,
            'budget_unit': 'actual_raw_attempts_per_model', 'union_budget_multiplier': 2,
            'models': list(MODELS), 'estimated': False}


def frozen_union(index):
    return {'ranks': index.union_ranks, 'raw_emissions': index.budget,
            'complete': True, 'metadata': {'models': list(MODELS),
            'per_model_budget': index.budget, 'max_total_attempts': 2*index.budget}}


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
        index = attacks.index(counts, purpose='selection')
        h = risk(index, history, [cfg['objective_budget']])
        p = risk(index, x['preview']['counts'], [cfg['objective_budget']], cfg['preview_users'])
        n = sum(history.values())
        predicted = (n*h['rates'][str(cfg['objective_budget'])] + cohort_size*p['rates'][str(cfg['objective_budget'])])/(n+cohort_size)
        frozen = sum(v for w, v in x['preview']['counts'].items() if w in frozen_prefix['ranks'])/cfg['preview_users']
        x['train_counts'] = counts
        outside = (n*h['outside_model_support_fraction']+cohort_size*p['outside_model_support_fraction'])/(n+cohort_size)
        x['row'].update(predicted_A1=predicted, predicted_outside=outside,
                        frozen_rate=frozen, history_actual_dual=h, preview_actual_dual=p, training_sha256=counts_hash(counts),
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
               x['row']['cost']['mean_extra_submissions'],
               len(x['rule'].exact)+len(x['rule'].shapes)+len(getattr(x['rule'], 'soft_shapes', ())), not x['row']['hold']))
    gain = reference['row']['predicted_A1'] - best['row']['predicted_A1']
    confirmation = None
    chosen, status = hold, 'hold_insufficient_selection_gain'
    if not best['row']['hold'] and gain >= cfg['min_gain']:
        confirmed = []
        for x in (reference, best):
            p = response.simulate(previews['confirm'], x['rule'], seed, patience=cfg.get('patience'))
            attach_base_cost(p, baseline_previews['confirm'])
            index = attacks.index(x['train_counts'], purpose='confirmation')
            h = risk(index, history, [cfg['objective_budget']])
            pr = risk(index, p['counts'], [cfg['objective_budget']], cfg['preview_users'])
            n = sum(history.values())
            confirmed.append({'cost': p['summary'], 'failures': cost_failures(p['summary'], cfg),
                              'A1': (n*h['rates'][str(cfg['objective_budget'])]+cohort_size*pr['rates'][str(cfg['objective_budget'])])/(n+cohort_size),
                              'outside': (n*h['outside_model_support_fraction']+cohort_size*pr['outside_model_support_fraction'])/(n+cohort_size),
                              'frozen': sum(v for w, v in p['counts'].items() if w in frozen_prefix['ranks'])/cfg['preview_users'],
                              'history_actual_dual': h, 'preview_actual_dual': pr})
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
                  'A1_actual_dual': None, 'F_actual_dual': risk(gate_index, state['history'], cfg['budgets'], users),
                  'F_actual_small': observed(frozen, state['history'], [cfg['frozen_budget']], users),
                  'eligibility_failures': ['developer_training_pending'],
                  'attack_failures': {'A1': 'Incomplete training role retained; no silent deletion or fit'}, 'actual_attacks': {}}
        result['eligibility_failures'] += cost_failures(result['cost'], cfg)
        write_json(finished, result)
        return result
    index = attacks.index(state['dev_history'], purpose='final')
    result = {'version': VERSION, 'arm': arm, 'status': 'complete', 'seed': seed,
              'timeline': state['timeline'], 'decisions': state['decisions'],
              'cost': summed_cost(state['costs']), 'cohort_costs': state['costs'],
              'distribution': concentration(state['history']),
              'A1_training_sha256': counts_hash(state['dev_history']),
              'A1_training_occurrences': sum(state['dev_history'].values()),
              'developer_simulated_exits': state['developer_pending'] if cfg.get('patience') else 0,
              'training_exit_handling': 'only completed simulated developer accounts have passwords; exits counted explicitly' if cfg.get('patience') else 'no dropped pending developer records',
              'A1_actual_dual': risk(index, state['history'], cfg['budgets'], users),
              'F_actual_dual': risk(gate_index, state['history'], cfg['budgets'], users),
              'F_actual_small': observed(frozen, state['history'], [cfg['frozen_budget']], users),
              'cohort_A1_actual_dual': [], 'actual_attacks': {}, 'attack_failures': {}}
    result['cost']['p90_extra_submissions'] = sorted(state['attempts'])[min(users-1, int(.9*users))]
    result['eligibility_failures'] = cost_failures(result['cost'], cfg)
    result['cost']['policy_updates'] = sum(a != b for a, b in zip(state['timeline'], state['timeline'][1:]))
    if cfg.get('cohort_aware', False) and not arm.startswith('cal_'):
        for targets, training in zip(state['final_batches'], state['dev_batches']):
            ci = attacks.index(training, purpose='final')
            result['cohort_A1_actual_dual'].append(risk(ci, targets, cfg['budgets'], users//len(cohorts)))
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
