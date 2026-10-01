"""F/A0/A1 with reusable Monte Carlo indexes and original-user denominators."""
from collections import Counter
from ai.pcfg_monte_carlo import Grammar, MonteCarloIndex
from core.monte_carlo_attack import MCRun, evaluate_mc, mc_difficulty
from experiments.dynamic_config import ROOT


def prepare_index(counts, cfg, progress=None):
    if progress:
        progress('训练 PCFG 并前置采样，建立可复用排名索引')
    grammar = Grammar.fit(counts, runtime_root=ROOT / 'reports' / 'pcfg_mc_runtime',
                          timeout=cfg['pcfg']['timeout_seconds'])
    return MonteCarloIndex(grammar, **cfg['monte_carlo'])


def attack_all_mc(cfg, development, arms, targets, pairs, pool, vocabulary, progress, frozen_index):
    from experiments.dynamic_pipeline import _development_response
    budgets, total = cfg['budgets'], cfg['data']['users']
    frozen = [MCRun(frozen_index, budgets[-1])]
    reports = {'by_strategy': {}, 'controls': {}, 'controls_A0_by_cohort': {},
               'paired_dynamic_minus_control': {}, 'guess_counts': {'controls_A1': {}}}
    ev = lambda runs, target, n=total: evaluate_mc(runs, target, budgets, n)
    baseline = ev(frozen, targets['baseline'])
    reports['baseline_F'] = reports['baseline_completed_F'] = baseline
    reports['by_strategy']['baseline'] = {level: baseline for level in ('F', 'A0', 'A1')}
    reports['baseline_attack_identity'] = 'no policy; F = A0 = A1'
    reports['guess_counts']['baseline_F'] = mc_difficulty(frozen, targets['baseline'], budgets[-1])
    reports['guess_counts']['baseline_completed_F'] = reports['guess_counts']['baseline_F']
    adaptive_dynamic = None
    fits = {frozen_index.grammar.metadata['training_sha256']: frozen_index}
    from core.corpus import counts_hash
    for name, arm in arms.items():
        if progress:
            progress('估计 ' + name + ' 的 F / A0 / A1 猜测曲线')
        known_cohorts = []
        for i, (rule, target) in enumerate(zip(arm['policies'], arm['cohort_targets'])):
            n = sum(arms['dynamic']['baseline_targets'][i].values())
            known_cohorts.append({'cohort_id': i + 1,
                'evaluation': ev([MCRun(frozen_index, budgets[-1], rule)], target, n)})
        known_points = []
        for j, budget in enumerate(budgets):
            parts = [r['evaluation']['minauto'][j] for r in known_cohorts]
            point = dict(parts[0])
            for key in ('hits', 'target_weight', 'unresolved_weight', 'low_sample_support_weight',
                        'outside_model_support_weight', 'pending_weight'):
                point[key] = sum(p[key] for p in parts)
            point['rate'] = point['hits'] / total
            known_points.append(point)
        known = {'minauto': known_points, 'method': 'policy-masked importance sampling; same frozen model'}
        train = _development_response(development['train'], arm['policies'], cfg, pool, vocabulary, 'train')
        key = counts_hash(train)
        if key not in fits:
            fits[key] = prepare_index(train, cfg, progress)
        adaptive = [MCRun(fits[key], budgets[-1])]
        levels = {'F': ev(frozen, targets[name]), 'A0': known, 'A1': ev(adaptive, targets[name])}
        reports['by_strategy'][name] = levels
        if name == 'dynamic':
            reports.update(levels)
            reports['A0_by_cohort'] = known_cohorts
            reports['A1_by_cohort'] = [{'cohort_id': i + 1,
                'evaluation': ev(adaptive, target, sum(arms['dynamic']['baseline_targets'][i].values()))}
                for i, target in enumerate(arm['cohort_targets'])]
            adaptive_dynamic = adaptive
            reports['guess_counts']['dynamic_F'] = mc_difficulty(frozen, targets[name], budgets[-1])
            reports['guess_counts']['dynamic_A1'] = mc_difficulty(adaptive, targets[name], budgets[-1])
        else:
            reports['controls'][name] = levels['A1']
            reports['controls_A0_by_cohort'][name] = known_cohorts
            reports['guess_counts']['controls_A1'][name] = mc_difficulty(adaptive, targets[name], budgets[-1])
    cumulative = {name: Counter() for name in (*arms, 'baseline')}
    stages = []
    for i, baseline_batch in enumerate(arms['dynamic']['baseline_targets']):
        cumulative['baseline'].update(baseline_batch)
        for name, arm in arms.items():
            cumulative[name].update(arm['cohort_targets'][i])
        n = sum(cumulative['baseline'].values())
        stages.append({'cohort_id': i+1, 'users': n,
                       'evaluations': {name: ev(frozen, target, n) for name, target in cumulative.items()}})
    reports['registration_stages_F'] = {'method': 'pre-sampled frozen PCFG; cumulative original-user denominator', 'stages': stages}
    adaptive_dynamic[0].frozen_index = frozen_index
    return reports, adaptive_dynamic, []
