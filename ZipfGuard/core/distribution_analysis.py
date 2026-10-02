"""Default distribution analysis: sampled CDF-Zipf shape, never identity likelihood."""
from __future__ import annotations

import math
import time
import numpy as np

from core.cdf_sampling import FIT_VERSION, fit_cdf_sampling, ranked_cdf, replicate_fit
from core.distributions import _validate_counts, risk_threshold, top_b_mass

MODEL_ID = 'cdf_sampling'
MODEL_NAME = 'CDF 采样拟合'


def sampling_diagnostics(counts, *, seed=42):
    """Fit the new method only; historical model comparisons are opt-in tools."""
    values = np.asarray(counts)
    started = time.perf_counter()
    new = fit_cdf_sampling(values, seed=seed)
    new['runtime_seconds'] = time.perf_counter()-started
    replication = replicate_fit(values, new, seeds=[seed+1001+i for i in range(5)])
    real = ranked_cdf(values)
    indices = np.unique(np.r_[np.arange(min(30, len(real))),
                            np.geomspace(1, len(real), min(250, len(real))).astype(int)-1,
                            len(real)-1])
    new['replication'] = {k:v for k,v in replication.items() if k != 'mean_cdf'}
    return {'fit_version': FIT_VERSION, 'selected_model': MODEL_ID,
            'users': int(values.sum()), 'unique': len(real), 'seed': seed,
            'sampling_fit': new, 'heldout_shape_check': None,
            'curves': [{'rank': int(i+1), 'empirical': float(real[i]),
                        'cdf_sampling': replication['mean_cdf'][i]} for i in indices]}


def analyze_sampling_counts(counts, *, q=.8, budget=100, bootstrap_repetitions=120,
                            seed=20260916, training_counts=None, validation_counts=None):
    original = _validate_counts(counts)
    if not 0 < q <= 1 or int(budget) != budget or budget < 0:
        raise ValueError('风险质量或猜测预算无效')
    if not 20 <= bootstrap_repetitions <= 2000:
        raise ValueError('Bootstrap 重复次数须介于 20 与 2000')
    if (training_counts is None) != (validation_counts is None):
        raise ValueError('training_counts 与 validation_counts 必须同时提供')
    rng = np.random.default_rng(seed)
    if training_counts is None:
        training = rng.binomial(original, .7)
        validation = original-training
        split_method = 'deterministic seeded 70/30 binomial split'
    else:
        training, validation = _validate_counts(training_counts), _validate_counts(validation_counts)
        if (len(training) != len(original) or len(validation) != len(original)
                or not np.array_equal(training+validation, original)):
            raise ValueError('训练、验证频次须与总频次等长，且两者相加等于总频次')
        split_method = 'predefined train/validation user split'
    if training.sum() == 0 or validation.sum() == 0:
        raise ValueError('样本不足以划分训练集和验证集')
    order = np.argsort(-training, kind='stable')
    training, validation = training[order], validation[order]
    total, size, train_n, val_n = int(original.sum()), len(original), int(training.sum()), int(validation.sum())
    fitted = fit_cdf_sampling(training, seed=seed)
    seeds = [seed+1001+i for i in range(5)]
    train_check = replicate_fit(training, fitted, seeds=seeds)
    val_check = replicate_fit(validation, fitted, seeds=seeds)
    val_check['scope'] = 'independent simulation draws against held-out, independently ranked accounts'
    model_cdf = np.asarray(train_check['mean_cdf'])
    empirical_cdf = ranked_cdf(training)
    # No likelihood/BIC: a ranked occupancy shape is not a probability law for
    # the fixed identities in training and validation.
    crossing = np.flatnonzero(model_cdf >= q)
    model_rank = int(crossing[0]+1) if len(crossing) else None
    model = {'id': MODEL_ID, 'name': MODEL_NAME, 'parameters': fitted['parameters'],
             'fit_version': FIT_VERSION, 'ks': train_check['mean_max_cdf_error'],
             'validation_ks': val_check['mean_max_cdf_error'],
             'validation_log_likelihood': None, 'validation_cross_entropy_bits': None,
             'log_likelihood': None, 'bic': None, 'aic': None, 'risk_rank': model_rank}
    indices = np.unique(np.r_[np.arange(min(30, len(model_cdf))),
                            np.geomspace(1, len(model_cdf), min(160, len(model_cdf))).astype(int)-1])
    curves = [{'rank': int(i+1), 'empirical_cdf': float(empirical_cdf[i]),
               MODEL_ID+'_cdf': float(model_cdf[i])} for i in indices]
    empirical = np.sort(original)[::-1]/total
    val_prob = validation/val_n
    thresholds, top_mass, fixed_mass = [], [], []
    for _ in range(bootstrap_repetitions):
        sample = np.sort(rng.multinomial(total, empirical))[::-1]
        thresholds.append(risk_threshold(sample, q))
        top_mass.append(float(sample[:budget].sum()/total))
        fixed_mass.append(float(rng.multinomial(val_n, val_prob)[:budget].sum()/val_n))
    ci = lambda samples: np.quantile(samples, [.025, .975]).tolist()
    epsilon = math.sqrt(math.log(40)/(2*val_n))
    frozen_mass = top_b_mass(val_prob, budget)
    return {'fit_version': FIT_VERSION, 'sample_size': total, 'support_size': size,
            'observed_categories': int(np.count_nonzero(original)),
            'train_size': train_n, 'validation_size': val_n, 'seed': seed,
            'split_method': split_method, 'selected_model': MODEL_ID,
            'selection_method': '项目默认 CDF 采样拟合；固定随机数搜索，独立种子复核',
            'models': [model], 'curves': curves, 'llr_comparisons': [],
            'sampling_fit': fitted,
            'training_replication': {k:v for k,v in train_check.items() if k != 'mean_cdf'},
            'validation_replication': {k:v for k,v in val_check.items() if k != 'mean_cdf'},
            'risk_threshold': {'q': q, 'empirical_rank': risk_threshold(empirical, q),
                'model_rank': model_rank, 'model_rank_scope': 'sample shape, not identity-aware guess rank',
                'bootstrap_ci95': ci(thresholds), 'budget': budget, 'effective_budget': min(budget,size),
                'oracle_top_b_mass': top_b_mass(empirical,budget), 'oracle_top_b_ci95': ci(top_mass),
                'heldout_fixed_order_top_b_mass': frozen_mass, 'heldout_top_b_ci95': ci(fixed_mass),
                'heldout_dkw_epsilon95': epsilon,
                'heldout_dkw_interval95': [max(0.,frozen_mass-epsilon),min(1.,frozen_mass+epsilon)]},
            'bootstrap': {'repetitions': bootstrap_repetitions, 'confidence': .95,
                          'type': 'empirical multinomial; fixed known support'},
            'sample_size_effect': [],
            'limitations': ['采样拟合描述频次形状，不输出具体口令概率，不报告似然或 BIC。',
                            '验证 CDF 按验证账户独立排序；固定攻击排序的 Top-B 质量单独计算。',
                            '若图示排名范围未达到 q，模型头部排名为空，不强制归一化补齐。']}
