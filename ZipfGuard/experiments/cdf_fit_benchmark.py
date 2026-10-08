"""Compare ranked CDF shape fits using anonymous, losslessly stored counts."""
from __future__ import annotations

import hashlib
import time
import numpy as np

from core.cdf_sampling import (FIT_VERSION, cdf_distance, fit_cdf_sampling,
                               ranked_cdf, replicate_fit, sorted_counts)
from core.distributions import fit_model


def counts_from_summary(summary):
    points = summary['full_rank_frequency']
    if not points or points[0][0] != 1:
        raise ValueError('缺少完整排名起点')
    counts = []
    previous_rank, previous_count = 0, None
    for rank, count in points:
        if (int(rank) != rank or int(count) != count or rank <= previous_rank
                or count <= 0 or (previous_count is not None and count > previous_count)):
            raise ValueError('频次平台端点无效')
        if rank > previous_rank+1 and count != previous_count:
            raise ValueError('频次曲线存在非平台缺口，不能据此重建完整数据')
        counts.extend([count] * (rank-previous_rank))
        previous_rank, previous_count = rank, count
    if len(counts) != summary['unique'] or sum(counts) != summary['users']:
        raise ValueError('完整频次曲线与账户数或不同口令数不符')
    return np.asarray(counts, dtype=np.int64)


def _old_fit(counts, name):
    start = time.perf_counter()
    result = fit_model(counts, name)
    result['runtime_seconds'] = time.perf_counter()-start
    return result


def compare_fits(counts, *, seed=42, heldout=True):
    start = time.perf_counter()
    values = sorted_counts(counts)
    seeds = [seed+1001+i for i in range(5)]
    models = [_old_fit(values, name) for name in ('zipf', 'cdf_zipf')]
    fitting_start = time.perf_counter()
    new = fit_cdf_sampling(values, seed=seed)
    new['runtime_seconds'] = time.perf_counter()-fitting_start
    replication = replicate_fit(values, new, seeds=seeds)
    new['replication'] = {k:v for k,v in replication.items() if k != 'mean_cdf'}
    real = ranked_cdf(values)
    indices = np.unique(np.r_[np.arange(min(30, len(values))),
                            np.geomspace(1, len(values), min(250, len(values))).astype(int)-1,
                            len(values)-1])
    curves = [{'rank': int(i+1), 'empirical': float(real[i]),
               'zipf': models[0]['cdf'][i], 'cdf_zipf': models[1]['cdf'][i],
               'cdf_sampling': replication['mean_cdf'][i]} for i in indices]
    checks = None
    if heldout:
        train = np.random.default_rng(seed+2001).binomial(values, .7)
        validation = values-train
        validation = validation[validation > 0]
        if train.sum() and len(validation) > 1 and np.count_nonzero(train) > 1:
            order = np.argsort(-train, kind='stable')
            train = train[order]
            independent_fit = fit_cdf_sampling(train, seed=seed)
            checks = {'training_accounts': int(train.sum()), 'validation_accounts': int(validation.sum()),
                      'split_seed': seed+2001, 'known_support_size': len(values),
                      'scope': '70/30 account split, independently ranked validation histogram; shape only',
                      'cdf_sampling': replicate_fit(validation, independent_fit, seeds=seeds)}
            checks['cdf_sampling'].pop('mean_cdf')
            checks['cdf_sampling']['scope'] = 'independent simulation draws against held-out accounts'
            checks['cdf_sampling']['parameters'] = independent_fit['parameters']
            # Both model families now generate a validation-sized sample and
            # re-rank it. This separates occupancy effects from objective choice.
            for name in ('zipf', 'cdf_zipf'):
                fitted = _old_fit(train, name)
                errors = [cdf_distance(validation, np.random.default_rng(s).multinomial(
                    int(validation.sum()), fitted['pmf'])) for s in seeds]
                checks[name] = {'max_cdf_errors': errors, 'mean_max_cdf_error': float(np.mean(errors)),
                                'worst_max_cdf_error': max(errors), 'parameters': fitted['parameters']}
    old_ks = models[0]['ks']
    return {'fit_version': FIT_VERSION, 'users': int(values.sum()), 'unique': len(values),
            'counts_sha256': hashlib.sha256(values.astype('<i8').tobytes()).hexdigest(),
            'seed': seed, 'runtime_seconds': time.perf_counter()-start,
            'previous_models': [{k:v for k,v in model.items() if k not in ('pmf','cdf')}
                                for model in models],
            'sampling_fit': new, 'heldout_shape_check': checks, 'curves': curves,
            'cdf_error_reduction_vs_analytic_zipf': 1-replication['mean_max_cdf_error']/old_ks if old_ks else None,
            'improves_full_sample_shape': replication['mean_max_cdf_error'] < old_ks,
            'improves_heldout_shape': (checks['cdf_sampling']['mean_max_cdf_error'] <
                                     checks['zipf']['mean_max_cdf_error']) if checks else None,
            'limitations': ['拟合目标是按频次重新排序后的样本分布形状，不输出具体口令的概率。',
                            'CDF 最大误差不是攻击模型的实测破解率，不能直接替换 PCFG 风险。',
                            '独立模拟种子复核采样波动；留出账户比较另列。',
                            '黄金分割用于离散采样误差，不保证找到全局最优。']}


def intervention_fit_diagnostics(report, *, compare_legacy=False):
    """Run after policy decisions; never feed target-fitted curves to attackers."""
    from web.intervention_presentation import comparison_labels
    comparison = report.get('google_round_zipf', {})
    summaries = [('baseline', '无政策', report['baseline']['distribution'])]
    # A legacy partial Google rollout is not a compliant Google baseline.
    if comparison.get('google_baseline', {}).get('target', {}).get('all_accounts_compliant'):
        all_arms = {**comparison['arms'], **report.get('site_controls', {})}
        summaries += [(key, label, all_arms[key]['final']['distribution'])
                      for key, label in comparison_labels(report)[1:]]
    results = []
    for key, label, summary in summaries:
        counts = counts_from_summary(summary)
        if len(counts) < 2 or counts.sum() < 20 or counts.sum() > 1_000_000:
            results.append({'key': key, 'label': label, 'status': 'skipped',
                            'reason': '诊断要求至少两个不同口令，账户总数介于 20 与 100 万'})
            continue
        from core.distribution_analysis import sampling_diagnostics
        fitter = compare_fits if compare_legacy else sampling_diagnostics
        results.append({'key': key, 'label': label, 'status': 'complete',
                        'fit': fitter(counts, seed=report['config']['seed'])})
    return {'fit_version': FIT_VERSION, 'results': results,
            'policy_comparison_valid': bool(comparison.get('google_baseline', {}).get(
                'target', {}).get('all_accounts_compliant')),
            'usage': ('fitted ranked CDF guides local action selection; attack estimates remain post-hoc'
                      if report.get('config', {}).get('schema_version', '').endswith('-v4') else
                      'distribution diagnostics only; decisions and attack estimates unchanged')}


def intervention_round_parameter_diagnostics(report):
    """Fit every realized Google-dynamic snapshot without feeding fits to policy selection."""
    comparison = report.get('google_round_zipf', {})
    arms = comparison.get('arms', {})
    dynamic = arms.get('google_dynamic', {})
    trajectory = dynamic.get('trajectory', [])
    if not trajectory or not comparison.get('common_google_start_verified'):
        return None
    existing = {row['key']: row['fit'] for row in report.get('distribution_fits', {}).get('results', [])
                if row.get('status') == 'complete'}
    rows = []
    for i, snapshot in enumerate(trajectory):
        if snapshot['round'] != i:
            raise ValueError('动态分布快照轮次不连续')
        summary = snapshot['distribution']
        counts = counts_from_summary(summary)
        if i == 0 and 'google_hold' in existing:
            fit = existing['google_hold']
        elif i == len(trajectory)-1 and 'google_dynamic' in existing:
            fit = existing['google_dynamic']
        else:
            from core.distribution_analysis import sampling_diagnostics
            fit = sampling_diagnostics(counts, seed=report['config']['seed'])
        params = fit['sampling_fit']['parameters']
        replication = fit['sampling_fit'].get('replication', {})
        rows.append({'round': i, 'parameters': {'c': params['c'], 's': params['s']},
                     'training_max_cdf_error': fit['sampling_fit']['training_max_cdf_error'],
                     'mean_max_cdf_error': replication.get('mean_max_cdf_error'),
                     'unique_passwords': summary['unique'],
                     'affected_rate': snapshot['ledger']['affected_rate']})
    if ('google_hold' in existing and
            rows[0]['parameters'] != existing['google_hold']['sampling_fit']['parameters']):
        raise AssertionError('逐轮图与 Google 固定对照使用了不同的起点拟合')
    scope = ('共同 Google 起点和每轮实际终态；相同拟合方法与种子；v4 以拟合前段累计占比选动作'
             if report.get('config', {}).get('schema_version', '').endswith('-v4') else
             '共同 Google 起点和每轮实际终态；相同拟合种子；仅作分布形状诊断')
    return {'fit_version': FIT_VERSION, 'method': 'CDF ranked-occupancy sampling',
            'scope': scope,
            'rows': rows}
