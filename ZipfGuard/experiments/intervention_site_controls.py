"""Whole-site mandatory controls, separate from the local notification budget."""
from collections import Counter
from policy.yahoo_japan import YahooJapanRule
from policy.local_actions import Action, Group
from policy.intervention_response import InterventionResponder
from policy.user_response import _rng
from policy.open_policy import edit_distance
from core.intervention_risk import InterventionRisk


def run_yahoo_control(initial, reference_words, evaluator, cfg, progress=None):
    from experiments.intervention_pipeline import snapshot, make_index
    rule = YahooJapanRule()
    responder = InterventionResponder(Counter(reference_words), dict(cfg['response'], nonresponse=0.))

    def migrate(population, stream):
        before = snapshot(population, evaluator, 0)
        ids = tuple(i for i, a in enumerate(population.accounts) if not rule.accepts(a.password))
        completions = 0
        if ids:
            action = Action(Group('all', '', 'Yahoo! JAPAN 全站不合规账户'), rule,
                            '15–32 字符，公开半角字符及组合限制', ids, len(ids))
            rows = responder.respond(population, action, cfg['seed'], stream)
            completions = sum(bool(row.get('explicit_completion')) for row in rows)
            for row in rows:
                if row['status'] != 'changed':
                    identifier = population.accounts[row['index']].identifier
                    new = rule.complete(row['new'], _rng(cfg['seed'], f'{stream}|completion|{identifier}'))
                    row.update(new=new, status='changed',
                               edit_cost=edit_distance(row['old'], new)/max(1, len(row['old']), len(new)))
                    completions += 1
            population.apply(rows)
        audit = {'required_modifications': len(ids), 'required_rate': len(ids)/population.total,
                 'explicit_completions': completions,
                 'noncompliant_remaining': sum(not rule.accepts(a.password) for a in population.accounts)}
        assert audit['noncompliant_remaining'] == 0
        return before, snapshot(population, evaluator, 1), audit

    if progress:
        progress('Yahoo! JAPAN：从原始账户进行一次全站强策略迁移')
    target = initial.clone()
    before, final, target_audit = migrate(target, 'yahoo-japan-target')
    from core.intervention_state import Population
    reference = Population(reference_words, 'yahoo-reference')
    _, _, reference_audit = migrate(reference, 'yahoo-japan-reference')
    adaptive = None
    if cfg['evaluation']['adaptive']:
        if progress:
            progress('Yahoo! JAPAN：在独立参考迁移分布上训练 A1')
        adaptive = InterventionRisk(make_index(reference.counts(), cfg), cfg['budgets'],
                                   cfg['risk_budget']).evaluate(target.counts())
    arm = {'method': 'yahoo_japan', 'label': 'Yahoo! JAPAN 强策略（全站一次）',
           'final': final, 'trajectory': [before, final],
           'rounds': [{'round': 1, 'action': {'selected': target_audit['required_modifications']}}],
           'stop_reason': 'whole_site_migration_complete', 'stop_label': '一次全站强策略迁移完成',
           'adaptive_reference_ledger': reference.ledger(),
           'attacks': {'F': final['risk'], 'A1': adaptive},
           'policy': rule.summary(), 'target_audit': target_audit, 'reference_audit': reference_audit,
           'premise': '与 Google 共同起点相同的全站强制合规前提：不拒绝，有限响应失败后显式补全；'
                      '不受局部 20% 通知预算或 F 面积接受门槛限制',
           'starting_state_sha256': initial.fingerprint()}
    return arm
