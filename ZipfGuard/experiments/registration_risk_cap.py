"""Attack-selective online popularity control, developed before test opening.

All accepted strings count, but only train-predicted guessable strings are capped.
Dynamic thresholds are screened and confirmed using cloned past-only states.
"""
from collections import Counter
from pathlib import Path
import time
import json

from policy.registration_comparators import PopularityOracle, simulate_online
from experiments.registration_joint_data import save_private, load_private, developer_stream
from experiments.registration_joint_pipeline import write_json, attach_base_cost, summed_cost, cost_failures
from experiments.registration_comparison import (base_rule, response_for, predictor, StaticMechanism,
    caps, score_counts, outside_fraction, preview_records, evaluate_state)


def joint_outside_fraction(counts, grammar, prefixes, total):
    """Outside BOTH generator domains; unhit and outside support are distinct."""
    meta = prefixes['omen']['metadata']
    alphabet = set(meta['alphabet'])
    supported = lambda w: meta['min_length'] <= len(w) <= meta['max_length'] and all(c in alphabet for c in w)
    return sum(n for w, n in counts.items() if not supported(w) and grammar.probability(w) <= 0)/total


class RiskSelectiveOracle(PopularityOracle):
    def __init__(self, base, rate, prefixes, budget, counts=None):
        super().__init__(base, rate, counts)
        self.prefixes = prefixes
        self.per = budget//2

    def decide(self, word):
        if not self.base.accepts(word):
            return False
        if not any(p['ranks'].get(word, self.per+1) <= self.per for p in self.prefixes.values()):
            return True
        return not self.counts[word] or self.counts[word] < self.rate*self.total


def simulate_risk_cap(cohorts, data, cfg, experiment, seed, path, prefixes, grammar, log,
                      *, fixed_rate=None):
    response, base = response_for(data, cfg), base_rule(data, experiment)
    checkpoint = path/'state.pkl.gz'
    if checkpoint.exists():
        state = load_private(checkpoint)
    else:
        state = {'t': 0, 'history': Counter(), 'batches': [], 'costs': [], 'attempts': [],
                 'timeline': [], 'decisions': [], 'rate': max(cfg['popularity_rates'])}
    for t in range(state['t'], len(cohorts)):
        threshold = fixed_rate if fixed_rate is not None else state['rate']
        if t and fixed_rate is None:
            recent_initial = cohorts[max(0, t-2):t]
            previews = {phase: preview_records(data['roles']['preview_'+phase], recent_initial,
                seed, t, 'risk-cap-'+phase, cfg['preview_users'], .5) for phase in ['select', 'confirm']}
            baselines = {phase: response.simulate(records, base, seed) for phase, records in previews.items()}
            history_rates = score_counts(state['history'], prefixes, cfg['budgets'], sum(state['history'].values()))
            def evaluate(r, phase):
                mechanism = RiskSelectiveOracle(base, r, prefixes, max(cfg['budgets']), state['history'])
                result = simulate_online(previews[phase], mechanism, response, seed)
                attach_base_cost(result, baselines[phase])
                n, h = cfg['preview_users'], sum(state['history'].values())
                risks = score_counts(result['counts'], prefixes, cfg['budgets'], n)
                return {'threshold': r, 'cost': result['summary'], 'rates': risks,
                    'cumulative_rates': [(h*a+len(cohorts[t])*b)/(h+len(cohorts[t])) for a,b in zip(history_rates, risks)],
                    'outside': joint_outside_fraction(result['counts'], grammar, prefixes, n),
                    'failures': cost_failures(result['summary'], caps(cfg, experiment))}
            reference = evaluate(threshold, 'select')
            candidates = [evaluate(r, 'select') for r in cfg['popularity_rates']]
            for row in candidates:
                if row['outside'] > reference['outside']+cfg['outside_increase']:
                    row['failures'].append('outside_support_increase')
                if any(a > b+cfg['small_budget_increase'] for a,b in zip(row['rates'][:-1], reference['rates'][:-1])):
                    row['failures'].append('smaller_budget_risk_increase')
            feasible = [row for row in candidates if not row['failures']]
            confirmation, status = None, 'hold_threshold'
            if feasible:
                winner = min(feasible, key=lambda row: (row['cumulative_rates'][-1], row['cost']['mean_extra_submissions'], -row['threshold']))
                if winner['threshold'] != threshold and (
                    reference['cumulative_rates'][-1]-winner['cumulative_rates'][-1] >= cfg['cf_min_gain'] or reference['failures']):
                    ref, win = evaluate(threshold, 'confirm'), evaluate(winner['threshold'], 'confirm')
                    failures = list(win['failures'])
                    if win['outside'] > ref['outside']+cfg['outside_increase']:
                        failures.append('confirmation_outside_support_increase')
                    if any(a > b+cfg['small_budget_increase'] for a,b in zip(win['rates'][:-1], ref['rates'][:-1])):
                        failures.append('confirmation_smaller_budget_risk_increase')
                    if ref['cumulative_rates'][-1]-win['cumulative_rates'][-1] < cfg['cf_min_gain'] and not reference['failures']:
                        failures.append('confirmation_insufficient_gain')
                    confirmation = {'reference': ref, 'winner': win, 'failures': failures, 'single_winner_only': True}
                    if not failures:
                        threshold, status = winner['threshold'], 'confirmed_threshold_update'
                    else:
                        status = 'hold_confirmation_failed'
            else:
                threshold, status = 1.0, 'base_fallback_no_feasible_threshold'
            # r=1 is effectively the common-base fallback; thresholds stay finite.
            write_json(path/f'candidate_audit_{t+1:02}.json', {'reference': reference, 'candidates': candidates,
                'confirmation': confirmation, 'status': status, 'chosen_threshold': threshold if threshold != float('inf') else None})
            state['decisions'].append({'batch': t+1, 'status': status, 'threshold': threshold if threshold != float('inf') else None})
        if t == 0 or threshold == float('inf'):
            result = response.simulate(cohorts[t], base, seed)
        else:
            mechanism = RiskSelectiveOracle(base, threshold, prefixes, max(cfg['budgets']), state['history'])
            result = simulate_online(cohorts[t], mechanism, response, seed)
        baseline = response.simulate(cohorts[t], base, seed)
        attach_base_cost(result, baseline)
        state['history'].update(result['counts'])
        state['batches'].append(result['counts'])
        state['costs'].append(result['summary'])
        state['attempts'].extend(result['attempts'])
        state['timeline'].append({**base.summary(), 'batch': t+1, 'risk_selective': True,
                                 'threshold_rate': threshold if threshold != float('inf') else None})
        state['t'], state['rate'] = t+1, threshold
        save_private(checkpoint, state)
        write_json(path/'progress.json', {'completed_batches': t+1, 'total_batches': len(cohorts),
                   'completed_users': sum(state['history'].values()), 'threshold': threshold if threshold != float('inf') else None})
        log(f'risk_budgeted: {t+1}/{len(cohorts)} registration batches')
    return state


def risk_developer_counts(state, data, cfg, experiment, seed, prefixes):
    response, base = response_for(data, cfg), base_rule(data, experiment)
    cohorts = developer_stream(data['roles']['train'], 'raw', seed, cfg['batches'], data['median_length'])
    counts = Counter()
    for t, records in enumerate(cohorts):
        r = state['timeline'][t]['threshold_rate']
        if t == 0 or r is None:
            result = response.simulate(records, base, seed)
        else:
            mechanism = RiskSelectiveOracle(base, r, prefixes, max(cfg['budgets']), counts)
            result = simulate_online(records, mechanism, response, seed)
        if result['summary']['pending_users']:
            raise RuntimeError('No partial developer fitting allowed')
        counts.update(result['counts'])
    return counts


def extend_job(request, base_job):
    from experiments.registration_joint_pipeline import AttackCache
    root, cfg = Path(request['root']), request['cfg']
    experiment, seed, phase = request['experiment'], request['seed'], request['phase']
    path = root/'runs'/phase/experiment/str(seed)
    data = load_private(root/'private/partition.pkl.gz')
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
    if phase == 'calibration':
        base_job['arms'] = {label: row for label, row in base_job['arms'].items()
                            if row['arm'] in ['refresh', 'schechter', 'malone']}
        candidates = [('risk_budgeted', None)]
        for label, r in candidates:
            screen = path/label/'screen.json'
            if screen.exists():
                result = json.loads(screen.read_text('utf-8'))
            else:
                state = simulate_risk_cap(cohorts, data, cfg, experiment, seed, path/label, prefixes, grammar, log, fixed_rate=r)
                cost = summed_cost(state['costs'])
                result = {'arm': label, 'parameters': {'rate': r}, 'cost': cost,
                    'risk': score_counts(state['history'], prefixes, cfg['budgets'], cfg['users']),
                    'eligibility_failures': cost_failures(cost, caps(cfg, experiment))}
                write_json(screen, result)
            base_job['arms'][label] = result
    else:
        output = path/'risk_budgeted/result.json'
        if output.exists():
            result = json.loads(output.read_text('utf-8'))
        else:
            state = simulate_risk_cap(cohorts, data, cfg, experiment, seed, path/'risk_budgeted', prefixes, grammar, log)
            # Explicitly replace only the developer transform for this new online
            # arm. The shared evaluator and observed-attack accounting are retained.
            import experiments.registration_comparison as base_module
            original = base_module.developer_counts
            try:
                base_module.developer_counts = lambda *args: risk_developer_counts(state, data, cfg, experiment, seed, prefixes)
                result = evaluate_state(state, 'risk_budgeted', data, cfg, experiment, seed, {}, cache, path/'risk_budgeted', log)
            finally:
                base_module.developer_counts = original
            write_json(output, result)
        base_job['arms']['risk_budgeted'] = result
    write_json(path/'summary.json', base_job)
    return base_job


def run_extended_job(request):
    from experiments.registration_comparison import run_comparison_job
    root = Path(request['root'])
    path = root/'runs'/request['phase']/request['experiment']/str(request['seed'])/'summary.json'
    baseline_path = path.with_name('baseline_summary.json')
    if path.exists():
        job = json.loads(path.read_text('utf-8'))
    elif baseline_path.exists():
        job = json.loads(baseline_path.read_text('utf-8'))
    elif request['phase'] == 'calibration':
        # Only the baselines retained in the report need new calibration runs.
        from experiments.registration_comparison import simulate_arm
        from experiments.registration_joint_pipeline import AttackCache
        cfg, experiment, seed = request['cfg'], request['experiment'], request['seed']
        data = load_private(root/'private/partition.pkl.gz')
        directory = path.parent
        def log(message):
            print(time.strftime('%Y-%m-%d %H:%M:%S')+f' [calibration/{experiment}/{seed}] '+message, flush=True)
        cache = AttackCache(root/'private/attacks'/experiment/str(seed), cfg, log)
        prefixes, grammar = predictor(data, cfg, experiment, seed, cache, root, log)
        records = data['roles'][f'calibration_{seed}']
        size = cfg['users']//cfg['batches']
        cohorts = [records[t*size:(t+1)*size] for t in range(cfg['batches'])]
        candidates = [(f'refresh_{k}', 'refresh', {'k': k}) for k in cfg['candidate_sizes']]
        if experiment == 'literature':
            candidates += [(f'schechter_{r}', 'schechter', {'rate': r}) for r in cfg['popularity_rates']]
            candidates += [('malone', 'malone', {})]
        job = {'phase': 'calibration', 'experiment': experiment, 'seed': seed, 'arms': {}}
        for label, arm, parameters in candidates:
            screen = directory/label/'screen.json'
            if screen.exists():
                result = json.loads(screen.read_text('utf-8'))
            else:
                state = simulate_arm(arm, cohorts, data, cfg, experiment, seed, parameters, directory/label,
                                     prefixes, grammar, log)
                cost = summed_cost(state['costs'])
                result = {'arm': arm, 'parameters': parameters, 'cost': cost,
                    'risk': score_counts(state['history'], prefixes, cfg['budgets'], cfg['users']),
                    'eligibility_failures': cost_failures(cost, caps(cfg, experiment))}
                write_json(screen, result)
            job['arms'][label] = result
    else:
        cfg = {**request['cfg'], 'arms': {e: [a for a in arms if a != 'risk_budgeted']
                for e, arms in request['cfg']['arms'].items()}}
        job = run_comparison_job({**request, 'cfg': cfg})
    return extend_job(request, job)
