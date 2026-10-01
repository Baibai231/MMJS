"""Choose the next registration rule using only completed cohorts and development data."""
from __future__ import annotations

import random
from collections import Counter

from core.uniformity import forecast_collision
from policy.dynamic_catalog import next_actions
from policy.user_response import simulate_users


def prediction_sample(counts, size, seed):
    words = sorted(counts)
    return random.Random(seed).choices(words, weights=[counts[w] for w in words],
                                       k=min(size, sum(counts.values())))


def select_next(current, history, *, development_train, preview_words, pool,
                vocabulary, seed, config, first_cohort_cost=None,
                next_batch_users=None):
    """The current/hold choice is a genuine candidate and may win."""
    head = set(sorted(development_train,
                      key=lambda w: (-development_train[w], w))[:1000])
    if config.get('candidate_source') == 'top15':
        from policy.site_catalog import site_rules
        # Choose only whole site profiles; no 960-template combinations.
        candidates = [('hold', current)] + [(name, rule) for name, rule in
            site_rules(config.get('include_recommendations', False)).items() if rule != current]
    else:
        candidates = next_actions(current, development_train, history,
                                  include_pattern_rules=config['include_pattern_rules'])
    batch_users = len(preview_words) if next_batch_users is None else next_batch_users
    rows = []
    for action, rule in candidates:
        projected = simulate_users(preview_words, rule, pool=pool,
                                   vocabulary=vocabulary, seed=seed,
                                   identifiers=(f'preview-{i}' for i in range(len(preview_words))),
                                   retry_report_after=config['retry_report_after'],
                                   candidate_limit=config['candidate_limit'],
                                   weights=tuple(config['response_weights']), records=False)
        s = projected['summary']
        n = s['completed_users']
        risk_proxy = (sum(count for word, count in projected['final'].items() if word in head) / n
                      if n else None)
        rows.append({'action': action, 'rule': rule,
                     'collision': forecast_collision(history, projected['final'], batch_users),
                     'head_risk_proxy': risk_proxy,
                     'modification_rate': s['modification_rate'],
                     'completion_rate': s['completion_rate'],
                     'pending_users': s['pending_users'],
                     'retry_reported_users': s['retry_reported_users'],
                     'extra_attempts': s['mean_extra_attempts']})
    hold = rows[0]
    for row in rows:
        row['feasible'] = bool(
            row['collision'] is not None
            and row['pending_users'] == 0
            and row['modification_rate'] <= config['max_modification_rate']
            and row['modification_rate'] - hold['modification_rate']
                <= config['max_incremental_modification']
            and (first_cohort_cost is None or row['modification_rate']
                 <= first_cohort_cost + config['max_late_cost_increase'])
            and row['head_risk_proxy'] is not None
            and row['head_risk_proxy'] <= hold['head_risk_proxy']
                + config['max_head_risk_regression'])
        if row['action'] != 'hold':
            relative = ((hold['collision'] - row['collision']) / hold['collision']
                        if hold['collision'] else 0)
            row['feasible'] &= relative >= config['min_relative_collision_gain']
            row['relative_collision_gain'] = relative
        else:
            row['relative_collision_gain'] = 0.
        checks = [
            (row['pending_users'] == 0, '候选生成有待处理用户'),
            (row['modification_rate'] <= config['max_modification_rate'], '超过修改率上限'),
            (row['modification_rate'] - hold['modification_rate'] <= config['max_incremental_modification'], '单次加严增加的修改率过高'),
            (first_cohort_cost is None or row['modification_rate'] <= first_cohort_cost + config['max_late_cost_increase'], '相对首批的修改率增加过高'),
            (row['head_risk_proxy'] is not None and row['head_risk_proxy'] <= hold['head_risk_proxy'] + config['max_head_risk_regression'], '开发热点风险代理回退过大'),
            (row['collision'] is not None, '预览不足以估计碰撞概率'),
        ]
        if row['action'] != 'hold':
            checks.append((row['relative_collision_gain'] >= config['min_relative_collision_gain'], '预测碰撞改善不足'))
        row['rejection_reasons'] = [reason for passed, reason in checks if not passed]
    possible = [row for row in rows if row['feasible']]
    winner = min(possible, key=lambda row:
                 (row['collision'], row['head_risk_proxy'], row['modification_rate'],
                  row['action'] != 'hold', row['action'])) if possible else hold
    public_rows = [{key: value for key, value in row.items() if key != 'rule'}
                   for row in rows]
    status = ('updated' if winner['action'] != 'hold' else
              'hold' if hold['feasible'] else 'no_feasible_update')
    return winner['rule'], {'action': winner['action'],
                            'status': status,
                            'history_users': sum(history.values()),
                            'first_cohort_cost_reference': first_cohort_cost,
                            'preview_users': len(preview_words),
                            'predicted_batch_users': batch_users,
                            'prediction_method': 'expected full-batch pair collisions from preview moments',
                            'candidates': public_rows,
                            'feedback': 'completed historical distribution and disjoint development preview'}
