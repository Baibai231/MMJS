"""Shared strategy selection and labels for the dynamic report and exports."""

PRESET_LABEL = '固定预设策略（长度≥8）'
STRATEGY_LABELS = {'dynamic': '动态策略', 'baseline': '无策略', 'fixed_preset': PRESET_LABEL}
from policy.site_catalog import site_labels
STRATEGY_LABELS.update(site_labels())

ATTACK_LABELS = {'F': '冻结攻击 F', 'A0': '仅知道规则 A0', 'A1': '自适应攻击 A1'}


def displayed_controls(result):
    controls = result.get('controls', {})
    if any(name.startswith('site_') for name in controls):
        return {name: arm for name, arm in controls.items() if name.startswith('site_')}
    # Old reports may provide this exact preset under its historical identifier.
    for name in ('fixed_preset', 'fixed_length8'):
        if name in controls:
            return {name: controls[name]}
    return {}


def attack_groups(result):
    attacks = result['attacks']
    if 'by_strategy' in attacks:
        return [(STRATEGY_LABELS[name], attacks['by_strategy'][name])
                for name in ('dynamic', 'baseline', 'fixed_preset', *[n for n in attacks['by_strategy'] if n.startswith('site_')])
                if name in attacks['by_strategy']]
    groups = [('动态策略', {level: attacks[level] for level in ATTACK_LABELS}),
              ('无策略', {'F': attacks['baseline_F']})]
    for name in displayed_controls(result):
        if name in attacks['controls']:
            groups.append((PRESET_LABEL, {'A1': attacks['controls'][name]}))
    return groups


def attack_entries(result):
    return [(f'{name} · {ATTACK_LABELS[level]}', levels[level])
            for name, levels in attack_groups(result)
            for level in ATTACK_LABELS if level in levels]


def attack_series(result):
    return [(name, [(p['budget'], p['rate']) for p in evaluation['minauto']])
            for name, evaluation in attack_entries(result)]
