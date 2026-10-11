"""Rebuild the published common start and run the disclosed HTPG assumption set."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.htpg_features import HTPGFeatureExtractor
from core.corpus import counts_hash
from core.intervention_state import Population
from core.registration import load_registration
from core.intervention_risk import InterventionRisk
from experiments.htpg_conditional import (ASSUMPTION_VERSION, run_conditional,
                                          replay_reference_round)
from experiments.htpg_paper_audit import _current_report, _point
from experiments.intervention_pipeline import make_index
from policy.intervention_response import InterventionResponder
from policy.local_actions import generate_actions
from policy.open_policy import edit_distance
from policy.user_response import satisfy_visible_rules, _rng


def log(message):
    print(datetime.now(timezone.utc).isoformat(timespec='seconds'), message, flush=True)


def rank_frequency_endpoints(counts):
    """Lossless rank-frequency plateaus without retaining any password string."""
    ranked = sorted(counts.values(), reverse=True)
    endpoints = []
    start = 1
    for rank, mass in enumerate(ranked, 1):
        if rank == 1:
            previous = mass
        elif mass != previous:
            endpoints.append([start, float(previous)])
            if rank-1 > start:
                endpoints.append([rank-1, float(previous)])
            start, previous = rank, mass
    if ranked:
        endpoints.append([start, float(previous)])
        if len(ranked) > start:
            endpoints.append([len(ranked), float(previous)])
    return endpoints


def common_start(report):
    cfg = report['config']
    data = cfg['data']
    source = (ROOT / data['path']).resolve()
    dataset = load_registration(source, source_format=data['format'], encoding=data['encoding'],
                                users=data['users'], development=data['development'],
                                seed=cfg['seed'], cohort_size=data['users'], progress=log)
    if dataset['metadata']['source_sha256'] != report['dataset']['source_sha256']:
        raise ValueError('source differs from published run')
    words = [word for batch in dataset['cohorts'] for word in batch]
    initial = Population(words)
    train = dataset['development']['train']
    reference_words = [word for word, mass in sorted(train.items()) for _ in range(mass)]
    reference = Population(reference_words, 'google-baseline-reference')
    log('读取固定 F 攻击模型；如缓存不存在，将按发布配置重建')
    frozen = make_index(train, cfg)
    evaluator = InterventionRisk(frozen, cfg['budgets'], cfg['risk_budget'])
    bootstrap_cfg = deepcopy(cfg)
    bootstrap_cfg['controller']['round_fraction'] = 1.
    bootstrap_cfg['controller']['total_fraction'] = 1.
    responder = InterventionResponder(Counter(reference_words),
                                      dict(cfg['response'], nonresponse=0.))
    def bootstrap(population, stream):
        choices = generate_actions(population, evaluator, bootstrap_cfg, fixed=True)
        if choices:
            outcomes = responder.respond(population, choices[0], cfg['seed'], stream)
            for row in outcomes:
                if row['status'] != 'changed':
                    account = population.accounts[row['index']]
                    new = satisfy_visible_rules(row['new'], min_length=8, classes=0,
                        rng=_rng(cfg['seed'], f'{stream}|completion|{account.identifier}'))
                    row.update(new=new, status='changed',
                        edit_cost=edit_distance(row['old'], new)/max(1, len(row['old']), len(new)))
            population.apply(outcomes, phase='google')
        if min(len(account.password) for account in population.accounts) < 8:
            raise AssertionError('the common eight-character start is incomplete')
    bootstrap(initial, 'google-baseline-target')
    bootstrap(reference, 'google-baseline-reference')
    expected = report['arms']['google_hold']['final']['account_state_sha256']
    actual = initial.account_fingerprint()
    if actual != expected:
        raise AssertionError(f'common start mismatch: {actual} != {expected}')
    expected_reference = report['arms']['google_hold']['adaptive_reference_ledger']
    reference_ledger = reference.ledger()
    if reference_ledger != expected_reference:
        raise AssertionError('independent reference start differs from published study')
    log('共同起点账户哈希与已发布实验一致')
    return initial.counts(), reference, evaluator, cfg, actual


def main():
    report, identity = _current_report()
    counts, reference, evaluator, cfg, start_hash = common_start(report)
    profile = ROOT/'resources'/'htpg_reference_v1.json'
    extractor = HTPGFeatureExtractor.from_profile(profile)
    rounds = len(report['arms']['google_dynamic']['rounds'])
    reference_start_hash = reference.account_fingerprint()
    replay_ledger = []
    def replay(number, children):
        replay_ledger.append(replay_reference_round(reference, children,
                                                   seed=cfg['seed'], round_number=number))
    final, history = run_conditional(counts, rounds=rounds, extractor=extractor,
                                     weighting='unique', progress=log, round_observer=replay)
    log('用共同固定 F 模型评价条件组终态')
    risk = evaluator.evaluate({word: float(mass) for word, mass in final.items()})
    reference_counts = reference.counts()
    if sum(reference_counts.values()) != reference.total:
        raise AssertionError('independent reference population changed size')
    log('在独立参考人群的整数响应终态训练 A1 PCFG／OMEN')
    adaptive_index = make_index(reference_counts, cfg)
    adaptive_risk = InterventionRisk(adaptive_index, cfg['budgets'], cfg['risk_budget'])
    a1_risk = adaptive_risk.evaluate({word: float(mass) for word, mass in final.items()})
    budget = cfg['risk_budget']
    current = report['arms']['google_dynamic']['final']['risk']
    hold = report['arms']['google_hold']['final']['risk']
    metrics = {key: _point(risk, budget, model) for key, model in
               (('F_pcfg_rate', 'pcfg'), ('F_omen_rate', 'markov'), ('F_union_rate', None))}
    metrics.update({key: _point(a1_risk, budget, model) for key, model in
                    (('A1_pcfg_rate', 'pcfg'), ('A1_omen_rate', 'markov'), ('A1_union_rate', None))})
    payload = {
        'schema_version': ASSUMPTION_VERSION, 'status': 'measured_conditional_assumptions',
        'full_author_reproduction_verified': False, 'paper_equations_preserved': True,
        'run_id': identity['run_id'], 'published_report_sha256': identity['report_sha256'],
        'start_account_state_sha256': start_hash, 'population_mass': sum(counts.values()),
        'reference_start_account_state_sha256': reference_start_hash,
        'reference_final_account_state_sha256': reference.account_fingerprint(),
        'reference_initial_ledger_matched_published': True,
        'reference_replay': replay_ledger,
        'reference_training_accounts': sum(reference_counts.values()),
        'reference_training_types': len(reference_counts),
        'A1_model_training_sha256': counts_hash(reference_counts),
        'rounds': history, 'metrics': metrics,
        'final_rank_frequency': rank_frequency_endpoints(final),
        'F_curves': {name: [{'budget': point['budget'], 'rate': point['rate']}
                            for point in (risk['minauto'] if name == 'union' else
                                          risk['attack_curves'][name]['minauto'])]
                     for name in ('union', 'pcfg', 'markov')},
        'A1_curves': {name: [{'budget': point['budget'], 'rate': point['rate']}
                             for point in (a1_risk['minauto'] if name == 'union' else
                                           a1_risk['attack_curves'][name]['minauto'])]
                      for name in ('union', 'pcfg', 'markov')},
        'current_F_union_rate': _point(current, budget),
        'hold_F_union_rate': _point(hold, budget),
        'F_union_delta_vs_current': metrics['F_union_rate']-_point(current, budget),
        'F_union_delta_vs_hold': metrics['F_union_rate']-_point(hold, budget),
        'current_A1_union_rate': _point(report['arms']['google_dynamic']['attacks']['A1'], budget),
        'hold_A1_union_rate': _point(report['arms']['google_hold']['attacks']['A1'], budget),
        'A1_union_delta_vs_current': metrics['A1_union_rate']-_point(
            report['arms']['google_dynamic']['attacks']['A1'], budget),
        'profile_sha256': hashlib.sha256(profile.read_bytes()).hexdigest(),
        'assumptions': {
            'feature_recognizers': 'core.htpg_features operational grammar, not author source',
            'lexicons': 'htpg_reference_v1.json VADER/Census alternative, not author lexicons',
            'weighting': 'one per unique password for IGR and side means',
            'lsd_scalar': 'number of L/D/S runs',
            'specplace_scalar': 'head=1, middle=2, tail=4 bit mask',
            'tail_categorical_target': 'unique-password modal category, lexical tie break',
            'undefined_igr': 'rank after defined scores; preserve all nine features',
            'feature_ties': 'stable FEATURES order',
            'lowercase_policy': 'toggle first character case toward tail-side prevalence',
            'date_use': 'append deterministic seeded uniform 1980..2020 year',
            'other_rewrites': 'existing project execute_suggestion rewrite functions, one policy per branch',
            'missing_or_failed_response': 'the corresponding 25% branch retains original password',
            'mass': 'exact expected fractions, not integer account assignments',
            'A1_reference': 'independent 60k accounts, published Google baseline reproduced exactly',
            'A1_policy': 'target-generated per-password two-response map replayed on independent reference only',
            'A1_integer_response': 'per-account deterministic SHA-256 seeded 50/25/25 branch draw; repeat changes allowed',
            'A1_training': 'PCFG and OMEN prefix retrained on integer independent reference endpoint; target counts never train A1',
            'comparison': 'same 100k target start; F and A1 use the same budgets and independent-reference protocol'},
        'plaintext_retained': False,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    directory = ROOT/'reports'/'htpg_paper'
    path = directory/'conditional_comparison.json'
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')
    path.write_bytes(raw)
    path.with_suffix('.json.sha256').write_text(hashlib.sha256(raw).hexdigest()+'  '+path.name+'\n',
                                                encoding='utf-8')
    from tools.plot_htpg_conditional import export_figures
    export_figures()
    from web.htpg_paper_interface import render_audit
    (directory/'report.html').write_text(render_audit(document=True), encoding='utf-8')
    log('已保存条件组指标与逐轮审计：'+str(path))
    print(json.dumps({'metrics': metrics, 'current_F': payload['current_F_union_rate'],
                      'current_A1': payload['current_A1_union_rate']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
