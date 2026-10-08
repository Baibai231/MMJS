"""Wasserstein-1 on normalized log ranks, relative to N singleton passwords.

The W1/CDF identity is from Panaretos & Zemel (2019), section 1.2.
The fixed support and log-rank geometry are this project's explicit choices.
"""
from functools import lru_cache
import math

METRIC = 'fitted_log_rank_wasserstein1'
IDEAL_LABEL = '理想分布（每个口令仅 1 人）'
REFERENCES = [
    {'title': 'Statistical Aspects of Wasserstein Distances',
     'authors': 'Panaretos & Zemel', 'year': 2019,
     'url': 'https://doi.org/10.1146/annurev-statistics-030718-104938',
     'formula_url': 'https://arxiv.org/html/1806.05500v3#S1.SS2'},
    {'title': 'The Science of Guessing: Analyzing an Anonymized Corpus of 70 Million Passwords',
     'authors': 'Joseph Bonneau', 'year': 2012,
     'url': 'https://www.ieee-security.org/TC/SP2012/papers/4681a538.pdf'},
]


@lru_cache(maxsize=128)
def ideal_log_area(n):
    if not isinstance(n, int) or n < 1:
        raise ValueError('理想参照需要正整数账户总数')
    if n == 1:
        return 1.
    return (n*math.log(n)-math.lgamma(n+1))/(n*math.log(n))


def distance_from_area(area, n):
    value = area-ideal_log_area(n)
    if value < -1e-10:
        raise ValueError('累计分布面积低于固定 N 的单例参照，请核对人数与排序')
    return max(0., value)


def ideal_metadata(n):
    return {'metric': METRIC, 'accounts': n, 'ideal_unique_passwords': n,
            'ideal_frequency': 1, 'ideal_log_area': ideal_log_area(n),
            'coordinate': 'x=ln(rank)/ln(N), fixed ranks 1..N',
            'distance': 'integral_0^1 |CDF_current(x)-CDF_N_singletons(x)| dx',
            'direction': 'lower is closer; zero means all observed passwords are unique',
            'scope': 'empirical frequency shape, not identity-aware attack security',
            'threshold': 'no universal security pass threshold; report distance and relative gap reduction',
            'references': REFERENCES}


def ideal_frequency_curve(n):
    return [(1, 1)] if n == 1 else [(1, 1), (n, 1)]


def attach_ideal_analysis(report):
    """Derive distances without changing any historical action or attack result."""
    from experiments.cdf_fit_benchmark import counts_from_summary
    from core.intervention_distribution import log_cdf_area, fitted_log_cdf_area_from_parameters
    n = report['baseline']['distribution']['users']
    report['ideal_distribution'] = ideal_metadata(n)
    fits = {row['key']: row['fit']['sampling_fit']['parameters']
            for row in report.get('distribution_fits', {}).get('results', [])
            if row.get('status') == 'complete'}
    seed = report['config']['seed']

    def annotate(state, score=None, already_distance=False, fit_key=None):
        counts = counts_from_summary(state['distribution'])
        if sum(counts) != n:
            raise ValueError('比较曲线的账户数必须相同')
        empirical = distance_from_area(log_cdf_area(counts), n)
        if score is None and fit_key in fits:
            score = fitted_log_cdf_area_from_parameters(fits[fit_key], n, seed)['score']
            already_distance = False
        state['ideal_distance'] = {'empirical': empirical,
            'fitted': (score if already_distance else distance_from_area(score, n)) if score is not None else None}

    annotate(report['baseline'], fit_key='baseline')
    for key, arm in {**report['google_round_zipf']['arms'], **report.get('site_controls', {})}.items():
        goal = arm.get('distribution_goal', {})
        native = goal.get('metric') == METRIC
        start = goal.get('start_fitted_distance') if native else goal.get('start_fitted_log_area')
        for i, state in enumerate(arm['trajectory']):
            score = start if i == 0 else arm['rounds'][i-1].get('selection_risk_after_same_model')
            fit_key = ('google_hold' if i == 0 and key.startswith('google_') else
                       'baseline' if i == 0 else key if i == len(arm['trajectory'])-1 else None)
            annotate(state, score, native, fit_key=fit_key)
        # JSON-loaded final and trajectory are equal objects, not shared references.
        final_score = goal.get('final_fitted_distance') if native else goal.get('final_fitted_log_area')
        annotate(arm['final'], final_score, native, fit_key=key)
