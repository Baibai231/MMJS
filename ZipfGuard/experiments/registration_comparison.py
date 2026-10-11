"""Prospective two-experiment comparison; all plaintext remains in private files."""
from collections import Counter
from dataclasses import replace
import bisect
import hashlib
import random
from pathlib import Path
import time

from core.corpus import parse_record, counts_hash
from policy.registration_joint import Preferences, JointRule, stable_seed
from policy.registration_comparators import (CompositionRule, PopularityOracle, MaloneMaher,
                                             simulate_online, exact_rule)
from experiments.registration_joint_data import load_private, save_private, developer_stream
from experiments.registration_joint_pipeline import (AttackCache, write_json, observed, risk,
    attach_base_cost, summed_cost, cost_failures, preview_records, trajectory)

VERSION = 'registration-counterfactual-comparison-v1'


def prepare_partition(source, old_path, root, cfg, log):
    path = root/'private/partition.pkl.gz'
    if path.exists():
        return load_private(path)
    old = load_private(old_path)
    excluded = {i for rows in old['roles'].values() for i, _ in rows}
    total = old['metadata']['valid_ASCII_occurrences']
    rng = random.Random(cfg['fresh_split_seed'])
    sizes = {f'{phase}_{seed}': cfg['users'] for phase in ['calibration', 'test'] for seed in cfg['seeds']}
    chosen, used = [], set(excluded)
    while len(chosen) < sum(sizes.values()):
        i = rng.randrange(total)
        if i not in used:
            chosen.append(i)
            used.add(i)
    allocation, cursor = {}, 0
    for role, n in sizes.items():
        for i in chosen[cursor:cursor+n]:
            allocation[i] = role
        cursor += n
    ids = sorted(chosen)
    roles = {r: [] for r in sizes}
    cursor = offset = rows = 0
    digest = hashlib.sha256()
    with Path(source).open('rb') as handle:
        for raw in handle:
            digest.update(raw)
            rows += 1
            try:
                word, count = parse_record(raw, 'password_with_count', 'latin-1')
            except ValueError:
                continue
            if any(not 32 <= ord(c) <= 126 for c in word):
                continue
            end = bisect.bisect_left(ids, offset+count, cursor)
            for i in ids[cursor:end]:
                roles[allocation[i]].append((i, word))
            cursor, offset = end, offset+count
            if rows % 2000000 == 0:
                log(f'fresh held-out partition: {rows:,} source rows scanned')
    if digest.hexdigest() != old['metadata']['source_sha256'] or cursor != len(ids) or offset != total:
        raise RuntimeError('Source hash/count mismatch; no held-out experiment permitted')
    for role, records in roles.items():
        rng.shuffle(records)
        if len(records) != sizes[role]:
            raise RuntimeError('Role size mismatch')
    roles.update({r: old['roles'][r] for r in ['train', 'tuning', 'preview_select', 'preview_confirm']})
    data = {'roles': roles, 'median_length': old['median_length'], 'metadata': {
        'source_sha256': digest.hexdigest(), 'fresh_split_seed': cfg['fresh_split_seed'],
        'excluded_previous_occurrences': len(excluded), 'all_roles_disjoint': True,
        'test_seeds_disjoint': True, 'role_sizes': {r: len(v) for r, v in roles.items()},
        'position_hashes': {r: hashlib.sha256(','.join(map(str, sorted(i for i, _ in v))).encode()).hexdigest()
                            for r, v in roles.items()},
        'previous_partition_sha256': hashlib.sha256(Path(old_path).read_bytes()).hexdigest(),
        'interpretation': 'frequency-declared occurrence users, not verified individuals'}}
    save_private(path, data)
    write_json(root/'data_manifest.json', data['metadata'])
    return data


def base_rule(data, experiment, common=True):
    train = Counter(w for _, w in data['roles']['train'])
    words = frozenset(w for w, _ in sorted(train.items(), key=lambda x: (-x[1], x[0]))[:1000]) if common else frozenset()
    return CompositionRule(minimum=8, common=words, github=experiment == 'website')


def response_for(data, cfg):
    return Preferences([w for _, w in data['roles']['train']], 8, cfg['weights'], 'mixed', cfg['candidate_limit'])


class StaticMechanism:
    def __init__(self, rule):
        self.rule = rule
    def begin(self, identifier, seed):
        pass
    def decide(self, word):
        return self.rule.accepts(word)
    def commit(self, word):
        pass


def caps(cfg, experiment):
    return {**cfg, **cfg['cost_limits'][experiment]}


def score_counts(counts, prefixes, budgets, total):
    rates = []
    for budget in budgets:
        # An equal TOTAL ensemble budget, unlike B attempts for EACH model.
        per = budget//2
        rates.append(sum(n for w, n in counts.items() if any(
            p['ranks'].get(w, per+1) <= per for p in prefixes.values()))/total)
    return rates


def outside_fraction(counts, grammar, total):
    return sum(n for w, n in counts.items() if grammar.probability(w) <= 0)/total


def predictor(data, cfg, experiment, seed, cache, root, log):
    path = root/'private'/f'predictor-{experiment}-{seed}.pkl.gz'
    if path.exists():
        counts = load_private(path)
    else:
        response = response_for(data, cfg)
        counts = response.simulate(data['roles']['train'], base_rule(data, experiment), seed)['counts']
        save_private(path, counts)
    prefixes = {m: cache.raw(counts, max(cfg['budgets']), m) for m in ['pcfg', 'omen']}
    if not all(p['complete'] for p in prefixes.values()):
        raise RuntimeError('Incomplete selection attack prefix')
    grammar, _ = cache.grammar(counts)
    return prefixes, grammar


def learned_order(records, response, base, seed, prefixes, budget):
    """First-block counterfactual benefit/cost heuristic. Interactions confirmed below."""
    benefit, cost, support = Counter(), Counter(), Counter()
    per = budget//2
    hit = lambda w: int(any(p['ranks'].get(w, per+1) <= per for p in prefixes.values()))
    for identifier, original in records:
        proposals = response.proposals(original, identifier, seed)
        first = None
        for attempt, w in enumerate(proposals):
            if base.accepts(w):
                first, first_attempt = w, attempt
                break
        if first is None:
            continue
        for next_attempt, w in enumerate(proposals, first_attempt+1):
            if w != first and base.accepts(w):
                benefit[first] += hit(first)-hit(w)
                cost[first] += next_attempt-first_attempt
                support[first] += 1
                break
    eligible = [w for w in support if support[w] >= 2 and benefit[w] > 0]
    return sorted(eligible, key=lambda w: (-benefit[w]/max(1, cost[w]), -benefit[w], w)), {
        'eligible_positive_benefit_strings': len(eligible), 'preview_records': len(records),
        'ranking': 'sum paired first-block attack benefit / sum extra submissions',
        'candidate_interactions': 'full shared preference simulation before selection and confirmation'}


def select_counterfactual(base, hold, history, recent, recent_initial, data, response,
                          prefixes, grammar, cfg, experiment, seed, t, cohort_size, audit_path):
    n = cfg['preview_users']
    previews = {phase: preview_records(data['roles']['preview_'+phase], recent_initial, seed, t,
                                       'cf-'+phase, n, .5) for phase in ['select', 'confirm']}
    order, ordering = learned_order(previews['select'], response, base, seed, prefixes, max(cfg['budgets']))
    proposals = [('hold', hold), ('base', base)]
    for k in cfg['candidate_sizes']:
        proposals.append((f'refresh-{k}', exact_rule(base, recent, k)))
        proposals.append((f'benefit-{k}', replace(base, exact=frozenset(order[:k]), k=k)))
    seen, unique = set(), []
    for label, rule in proposals:
        if rule.signature() not in seen:
            seen.add(rule.signature())
            unique.append((label, rule))
    base_previews = {phase: response.simulate(records, base, seed) for phase, records in previews.items()}
    history_rates = score_counts(history, prefixes, cfg['budgets'], max(1, sum(history.values())))
    def evaluate(rule, phase):
        p = response.simulate(previews[phase], rule, seed)
        attach_base_cost(p, base_previews[phase])
        rates = score_counts(p['counts'], prefixes, cfg['budgets'], n)
        h = sum(history.values())
        return {'cost': p['summary'], 'failures': cost_failures(p['summary'], caps(cfg, experiment)),
                'rates': rates, 'cumulative_rates': [(h*a+cohort_size*b)/(h+cohort_size)
                                                  for a, b in zip(history_rates, rates)],
                'outside': outside_fraction(p['counts'], grammar, n)}
    rows = [{'label': label, 'rule': rule.summary(), **evaluate(rule, 'select')} for label, rule in unique]
    reference = rows[0]
    for row in rows:
        if row['outside'] > reference['outside']+cfg['outside_increase']:
            row['failures'].append('outside_support_increase')
        if any(a > b+cfg['small_budget_increase'] for a, b in zip(row['rates'][:-1], reference['rates'][:-1])):
            row['failures'].append('smaller_budget_risk_increase')
    feasible = [(row, rule) for row, (_, rule) in zip(rows, unique) if not row['failures']]
    chosen, confirmation = hold, None
    status = 'hold'
    if feasible:
        winner, candidate = min(feasible, key=lambda x: (x[0]['cumulative_rates'][-1],
             x[0]['cost']['mean_extra_submissions'], x[0]['rule']['exact_size']))
        if candidate.signature() != hold.signature() and (
                reference['cumulative_rates'][-1]-winner['cumulative_rates'][-1] >= cfg['cf_min_gain']
                or reference['failures']):
            ref, win = evaluate(hold, 'confirm'), evaluate(candidate, 'confirm')
            failures = list(win['failures'])
            if win['outside'] > ref['outside']+cfg['outside_increase']:
                failures.append('confirmation_outside_support_increase')
            if any(a > b+cfg['small_budget_increase'] for a, b in zip(win['rates'][:-1], ref['rates'][:-1])):
                failures.append('confirmation_smaller_budget_risk_increase')
            if ref['cumulative_rates'][-1]-win['cumulative_rates'][-1] < cfg['cf_min_gain'] and not reference['failures']:
                failures.append('confirmation_insufficient_gain')
            confirmation = {'reference': ref, 'winner': win, 'failures': failures,
                            'single_winner_only': True, 'separate_preview_roles': True}
            if not failures:
                chosen, status = candidate, 'confirmed_'+winner['label']
            else:
                status = 'hold_confirmation_failed'
    else:
        chosen, status = base, 'all_infeasible_base_fallback'
    write_json(audit_path, {'status': status, 'candidates': rows, 'ordering': ordering,
                            'confirmation': confirmation, 'chosen': chosen.summary(),
                            'predictor': 'shared fixed base-conditioned train-only PCFG+OMEN raw prefixes',
                            'past_accounts_retained': sum(history.values())})
    return chosen, {'batch': t+1, 'status': status, 'actual_size': len(chosen.exact)}


def simulate_arm(arm, cohorts, data, cfg, experiment, seed, parameters, path,
                 prefixes, grammar, log):
    checkpoint = path/'state.pkl.gz'
    response = response_for(data, cfg)
    base = base_rule(data, experiment, common=arm != 'website_core')
    if checkpoint.exists():
        state = load_private(checkpoint)
    else:
        state = {'t': 0, 'history': Counter(), 'batches': [], 'rule': base,
                 'costs': [], 'attempts': [], 'timeline': [], 'decisions': [],
                 'mechanism': None, 'warm_proposals': Counter(), 'rule_archive': {}}
    for t in range(state['t'], len(cohorts)):
        rule = state['rule']
        recent = sum(state['batches'][-2:], Counter())
        if t:
            if arm in ['fixed', 'refresh'] and (arm == 'refresh' or t == 1):
                rule = exact_rule(base, recent, parameters['k'])
            elif arm == 'counterfactual':
                rule, decision = select_counterfactual(base, rule, state['history'], recent,
                    cohorts[max(0, t-2):t], data, response, prefixes, grammar, cfg, experiment,
                    seed, t, len(cohorts[t]), path/f'candidate_audit_{t+1:02}.json')
                state['decisions'].append(decision)
        if arm in ['schechter', 'malone']:
            if t == 0:
                # Common first-batch bootstrap. Captures ALL proposals for F,
                # accepted counts for the popularity oracle, without requerying.
                actual = simulate_online(cohorts[t], StaticMechanism(base), response, seed, capture_proposals=True)
                state['warm_proposals'] = actual['proposed']
            else:
                if state['mechanism'] is None:
                    state['mechanism'] = (PopularityOracle(base, parameters['rate'], state['history'])
                        if arm == 'schechter' else MaloneMaher(base, state['warm_proposals']))
                actual = simulate_online(cohorts[t], state['mechanism'], response, seed)
        else:
            actual = response.simulate(cohorts[t], rule, seed)
        baseline = response.simulate(cohorts[t], base_rule(data, experiment), seed)
        attach_base_cost(actual, baseline)
        state['history'].update(actual['counts'])
        state['batches'].append(actual['counts'])
        state['costs'].append(actual['summary'])
        state['attempts'].extend(actual['attempts'])
        state['timeline'].append({**rule.summary(), 'batch': t+1,
            'online_mechanism': arm if arm in ['schechter', 'malone'] else None,
            'threshold_rate': parameters.get('rate') if arm == 'schechter' else None})
        state['rule'], state['t'] = rule, t+1
        state['rule_archive'][t] = rule
        save_private(checkpoint, state)
        write_json(path/'progress.json', {'completed_batches': t+1, 'total_batches': len(cohorts),
                   'completed_users': sum(state['history'].values()), 'last_rule': state['timeline'][-1]})
        log(f'{arm}: {t+1}/{len(cohorts)} registration batches')
    return state


def developer_counts(state, arm, data, cfg, experiment, seed, parameters):
    response = response_for(data, cfg)
    base = base_rule(data, experiment, common=arm != 'website_core')
    train = developer_stream(data['roles']['train'], 'raw', seed, cfg['batches'], data['median_length'])
    counts, mechanism = Counter(), None
    for t, records in enumerate(train):
        if arm in ['schechter', 'malone']:
            if t == 0:
                result = simulate_online(records, StaticMechanism(base), response, seed, capture_proposals=True)
                mechanism = (PopularityOracle(base, parameters['rate'], result['counts']) if arm == 'schechter'
                             else MaloneMaher(base, result['proposed']))
            else:
                result = simulate_online(records, mechanism, response, seed)
        else:
            # Public rule membership is available to the adaptive attacker. Only
            # developer training users are transformed, never target plaintext.
            timeline = state['timeline'][t]
            rule = state['rule'] if t == len(train)-1 else state.get('rule_archive', {}).get(t)
            if rule is None:
                raise RuntimeError('Missing public rule archive for developer attack training')
            result = response.simulate(records, rule, seed)
        if result['summary']['pending_users']:
            raise RuntimeError('Pending developer records: cannot silently fit partial data')
        counts.update(result['counts'])
    return counts


def evaluate_state(state, arm, data, cfg, experiment, seed, parameters, cache, path, log):
    users = sum(s['initial_users'] for s in state['costs'])
    counts = developer_counts(state, arm, data, cfg, experiment, seed, parameters)
    train_path = path/'developer_counts.pkl.gz'
    save_private(train_path, counts)
    index = cache.index(counts, cfg['final_samples'], 'final')
    result = {'version': VERSION, 'arm': arm, 'experiment': experiment, 'seed': seed,
              'status': 'complete', 'cost': summed_cost(state['costs']),
              'timeline': state['timeline'], 'decisions': state['decisions'],
              'A1_MC': risk(index, state['history'], cfg['budgets'], users),
              'A1_training_sha256': counts_hash(counts), 'A1_training_occurrences': sum(counts.values()),
              'actual_attacks': {}, 'attack_failures': {}}
    prefixes = {}
    for model in ['pcfg', 'omen']:
        prefix = cache.raw(counts, max(cfg['budgets']), model)
        if not prefix['complete']:
            raise RuntimeError(f'{model} incomplete actual prefix')
        prefixes[model] = prefix
        result['actual_attacks'][model] = observed(prefix, state['history'], cfg['budgets'], users)
    result['actual_attacks']['union_equal_total_B'] = {'points': [
        {'budget': b, 'per_model_budget': b//2, 'max_total_attempts': b,
         'rate_per_initial_user': r, 'hits': round(r*users), 'initial_users': users}
        for b, r in zip(cfg['budgets'], score_counts(state['history'], prefixes, cfg['budgets'], users))],
        'estimated': False}
    result['eligibility_failures'] = cost_failures(result['cost'], caps(cfg, experiment))
    write_json(path/'result.json', result)
    return result


def run_comparison_job(request):
    root, cfg = Path(request['root']), request['cfg']
    experiment, seed, phase = request['experiment'], request['seed'], request['phase']
    data = load_private(root/'private/partition.pkl.gz')
    path = root/'runs'/phase/experiment/str(seed)
    path.mkdir(parents=True, exist_ok=True)
    def log(message):
        line = time.strftime('%Y-%m-%d %H:%M:%S')+f' [{phase}/{experiment}/{seed}] '+message
        print(line, flush=True)
        with (path/'events.log').open('a', encoding='utf-8') as handle:
            handle.write(line+'\n')
    cache = AttackCache(root/'private/attacks'/experiment/str(seed), cfg, log)
    prefixes, grammar = predictor(data, cfg, experiment, seed, cache, root, log)
    records = data['roles'][f'{phase}_{seed}']
    size = cfg['users']//cfg['batches']
    cohorts = [records[t*size:(t+1)*size] for t in range(cfg['batches'])]
    frozen = root/'frozen_parameters'/f'{experiment}.json'
    parameters = __import__('json').loads(frozen.read_text('utf-8')) if frozen.exists() else {}
    results = {}
    if phase == 'calibration':
        arms = [(f'{arm}_{k}', arm, {'k': k}) for arm in ['fixed', 'refresh']
                for k in cfg['candidate_sizes']]
        if experiment == 'literature':
            arms += [(f'schechter_{r}', 'schechter', {'rate': r}) for r in cfg['popularity_rates']]
            arms += [('malone', 'malone', {})]
        arms += [('counterfactual', 'counterfactual', {})]
        for label, arm, params in arms:
            output = path/label/'screen.json'
            if output.exists():
                results[label] = __import__('json').loads(output.read_text('utf-8'))
                continue
            state = simulate_arm(arm, cohorts, data, cfg, experiment, seed, params, path/label,
                                 prefixes, grammar, log)
            cost = summed_cost(state['costs'])
            result = {'arm': arm, 'parameters': params, 'cost': cost,
                      'risk': score_counts(state['history'], prefixes, cfg['budgets'], cfg['users']),
                      'eligibility_failures': cost_failures(cost, caps(cfg, experiment)),
                      'selection_attacks': 'frozen base-conditioned PCFG+OMEN; not final adapted attack'}
            write_json(output, result)
            results[label] = result
    else:
        if not parameters:
            raise RuntimeError('Calibration must be frozen before test access')
        arms = cfg['arms'][experiment]
        for arm in arms:
            output = path/arm/'result.json'
            if output.exists():
                results[arm] = __import__('json').loads(output.read_text('utf-8'))
                continue
            if arm == 'current_joint':
                # Invoke unchanged published project controller and full attacker.
                old_cfg = {**cfg['current_config'], 'cohort_aware': False}
                old_data = {**data, 'roles': {**data['roles'], 'test': records}}
                raw_train = Counter(w for _, w in data['roles']['train'])
                frozen_prefix = cache.raw(raw_train, old_cfg['frozen_budget'], 'pcfg')
                gate = cache.index(raw_train, old_cfg['final_samples'], 'frozen')
                dev = developer_stream(data['roles']['train'], 'raw', seed, cfg['batches'], data['median_length'])
                result = trajectory('joint', cohorts, dev, old_data, old_cfg, seed,
                    {'fixed_pair': [1000, 0]}, path, cache, frozen_prefix, gate, log)
                result = {**result, 'arm': arm, 'experiment': experiment}
                if result['status'] != 'complete':
                    raise RuntimeError('Original current method has missing attack results')
                write_json(output, result)
            else:
                params = parameters.get(arm, parameters['refresh'])
                state = simulate_arm(arm, cohorts, data, cfg, experiment, seed, params, path/arm,
                                     prefixes, grammar, log)
                result = evaluate_state(state, arm, data, cfg, experiment, seed, params, cache, path/arm, log)
            results[arm] = result
    summary = {'phase': phase, 'experiment': experiment, 'seed': seed, 'arms': results}
    write_json(path/'summary.json', summary)
    return summary
