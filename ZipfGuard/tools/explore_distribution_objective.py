"""Offline research diagnostic; never changes a policy, report or publication pointer.

Uses only anonymous frequency summaries and stored CDF parameters. No accounts
are migrated and no attack model is trained. The proposed objective is NOT the
production controller's objective. Run from ZipfGuard with the existing venv.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.cdf_sampling import frequency_sample, ranked_cdf
from experiments.cdf_fit_benchmark import counts_from_summary


def log_cdf_area(counts):
    """Integral of step ranked CDF against d log(k), k in [1,N]. Lower is better.

    Common upper bound N is the fixed account count, not the current number of
    distinct passwords. This is a sample-shape score, not true population risk.
    """
    values = np.asarray(counts, dtype=float)
    if (values.ndim != 1 or values.size == 0 or not np.isfinite(values).all()
            or (values < 0).any() or (values != np.floor(values)).any()):
        raise ValueError('Expected finite nonnegative integer frequencies')
    values = np.sort(values[values > 0])[::-1]
    n = int(values.sum())
    if n < 2:
        raise ValueError('At least two accounts required')
    ranks = np.arange(1, len(values)+1, dtype=float)
    return float(np.dot(values/n, np.log(n/ranks))/math.log(n))


def cdf_comparison(before, after):
    n = int(sum(before))
    if n != int(sum(after)):
        raise ValueError('Fixed population required')
    delta = ranked_cdf(before, n) - ranked_cdf(after, n)
    return {'empirical_gain': log_cdf_area(before)-log_cdf_area(after),
            'all_rank_nonworsening': bool(np.min(delta) >= -1e-12),
            'strict_improvement_somewhere': bool(np.max(delta) > 1e-12),
            'largest_cdf_improvement': float(np.max(delta)),
            'largest_cdf_deterioration': float(max(0, -np.min(delta)))}


def partitions(n, maximum=None):
    if n == 0:
        yield []
    else:
        for first in range(min(n, maximum or n), 0, -1):
            for rest in partitions(n-first, first):
                yield [first]+rest


def verify_math():
    transfers = 0
    for n in range(2, 11):
        weights = np.log(np.arange(2, n+1)/np.arange(1, n))/math.log(n)
        for partition in partitions(n):
            integral = float(np.dot(ranked_cdf(partition, n)[:-1], weights))
            assert abs(integral-log_cdf_area(partition)) < 1e-12
            values = partition+[0]
            for i, source in enumerate(values):
                for j, destination in enumerate(values):
                    if source >= destination+2:
                        changed = values.copy()
                        changed[i] -= 1
                        changed[j] += 1
                        comparison = cdf_comparison(values, changed)
                        assert comparison['all_rank_nonworsening']
                        assert comparison['empirical_gain'] > 0
                        transfers += 1
        assert abs(log_cdf_area([n])-1) < 1e-12
    plateau = cdf_comparison([5, 3, 2], [5, 2, 2, 1])
    assert plateau['empirical_gain'] > 0  # Top-1 does not change.
    first = cdf_comparison([6, 4], [5, 5])
    repeat = cdf_comparison([5, 5], [4, 6])
    assert first['empirical_gain'] > 0 and repeat['empirical_gain'] < 0
    return {'status': 'passed', 'equalizing_one_account_transfers_checked': transfers,
            'population_sizes': [2, 10], 'top1_plateau_example': plateau,
            'overfilled_destination_example': {'first': first, 'repeat': repeat},
            'scope': 'synthetic mathematics only; no user response or rank safeguard claim'}


def diagnose(report_path):
    raw = report_path.read_bytes()
    report = json.loads(raw)
    n = report['arms']['google_dynamic']['trajectory'][0]['distribution']['users']
    seed = report['config']['seed']
    seeds = [seed+1001, seed+1002, seed+1003]
    uniforms = [np.maximum(np.random.default_rng(s).random(n), np.finfo(float).tiny)
                for s in seeds]

    def sampled(params):
        return [frequency_sample(params['c'], params['s'], u) for u in uniforms]

    def fitted(params):
        return [log_cdf_area(sample) for sample in sampled(params)]

    empirical_arms = {}
    for key, arm in report['arms'].items():
        rows, previous = [], None
        for snapshot in arm['trajectory']:
            counts = counts_from_summary(snapshot['distribution'])
            row = {'round': snapshot['round'], 'score': log_cdf_area(counts),
                   'local_affected_rate': snapshot['ledger']['adaptive_affected_rate']}
            if previous is not None:
                row.update(cdf_comparison(previous, counts))
            rows.append(row)
            previous = counts
        empirical_arms[key] = rows

    parameter_rows = {row['round']: row for row in report['round_parameter_fits']['rows']}
    dynamic_rows, previous_fit_scores = [], None
    for snapshot in report['arms']['google_dynamic']['trajectory']:
        counts = counts_from_summary(snapshot['distribution'])
        params = parameter_rows[snapshot['round']]['parameters']
        samples = sampled(params)
        scores = [log_cdf_area(s) for s in samples]
        real_cdf = ranked_cdf(counts, n)
        max_errors = [float(np.max(np.abs(ranked_cdf(s, n)-real_cdf))) for s in samples]
        row = {'round': snapshot['round'], 'parameters': params,
               'empirical_score': log_cdf_area(counts), 'fitted_score': float(np.mean(scores)),
               'fit_draw_scores': scores, 'fit_draw_max_cdf_errors': max_errors}
        if previous_fit_scores is not None:
            gains = [a-b for a,b in zip(previous_fit_scores, scores)]
            row.update(fitted_gain=float(np.mean(gains)),
                       paired_fit_draw_gain_range=[min(gains), max(gains)])
        dynamic_rows.append(row)
        previous_fit_scores = scores

    terminal = []
    for candidate in report['arms']['google_dynamic']['terminal_candidate_audit']:
        if not candidate.get('fitted_after'):
            continue
        scores = fitted(candidate['fitted_after']['parameters'])
        gains = [a-b for a,b in zip(previous_fit_scores, scores)]
        terminal.append({'group': candidate['action']['group'],
                         'rule': candidate['action']['rule'],
                         'selected': candidate['action']['selected'],
                         'old_fitted_topk_gain': candidate['predicted_fitted_cdf_gain'],
                         'old_empirical_topk_gain': candidate['predicted_empirical_cdf_gain'],
                         'proposed_fitted_area_gain': float(np.mean(gains)),
                         'paired_fit_draw_gain_range': [min(gains), max(gains)]})
    return {'protocol': 'distribution-objective-exploration-v1',
            'source_report_sha256': hashlib.sha256(raw).hexdigest(),
            'diagnostic_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'cdf_code_sha256': hashlib.sha256((ROOT/'core/cdf_sampling.py').read_bytes()).hexdigest(),
            'users': n, 'fit_draw_seeds': seeds, 'mathematical_checks': verify_math(),
            'scope': 'retrospective scoring, not a new intervention experiment or validated controller',
            'uncertainty': 'ranges across three common simulation draws; NOT confidence intervals',
            'terminal_limit': 'stored fit is from first response simulation only; cannot establish mean action benefit',
            'empirical_arms': empirical_arms, 'dynamic_fitted_trajectory': dynamic_rows,
            'terminal_candidates': terminal,
            'terminal_summary': {'count': len(terminal),
                'old_fitted_positive': sum(r['old_fitted_topk_gain'] > 1e-6 for r in terminal),
                'area_mean_positive': sum(r['proposed_fitted_area_gain'] > 1e-12 for r in terminal),
                'area_positive_in_all_fit_draws': sum(r['paired_fit_draw_gain_range'][0] > 1e-12 for r in terminal)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.report.resolve() == args.output.resolve() or args.output.exists():
        parser.error('Use a new output path; existing artifacts are never overwritten')
    result = diagnose(args.report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'checks': result['mathematical_checks'],
                      'terminal_summary': result['terminal_summary'],
                      'dynamic': result['dynamic_fitted_trajectory']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
