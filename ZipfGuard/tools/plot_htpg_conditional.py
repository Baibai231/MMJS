"""Export two comparable, standalone SVGs from verified aggregate results."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.htpg_paper_audit import _current_report
from tools.run_dynamic_study import svg_only

OUT = ROOT/'reports'/'htpg_paper'


def _conditional():
    path = OUT/'conditional_comparison.json'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != path.with_suffix('.json.sha256').read_text().split()[0]:
        raise ValueError('conditional report checksum mismatch')
    return json.loads(raw)


def export_figures():
    conditional = _conditional()
    report, identity = _current_report(conditional['run_id'])
    if identity['report_sha256'] != conditional['published_report_sha256']:
        raise ValueError('published comparison report changed')
    arms = report['arms']
    start = arms['google_hold']['final']['account_state_sha256']
    if start != conditional['start_account_state_sha256']:
        raise ValueError('comparison does not share the same start')
    labels = ('共同 8 字符起点', '当前动态方法', 'HTPG 正文算法＋补全假设')
    initial = arms['google_hold']['final']
    current = arms['google_dynamic']['final']
    rank_series = [
        (labels[0], initial['distribution']['full_rank_frequency']),
        (labels[1], current['distribution']['full_rank_frequency']),
        (labels[2], conditional['final_rank_frequency']),
    ]
    if not conditional['final_rank_frequency'] or any(not points for _, points in rank_series):
        raise ValueError('missing rank-frequency data')
    lowest_mass = min(mass for _, points in rank_series for _, mass in points)
    rank_svg = svg_only(rank_series, xlabel='口令频次排名 r', ylabel='口令频次／期望质量',
                        log=True, log_y=True, markers=False,
                        x_format='count', y_format='count' if lowest_mass >= 1 else 'decimal6')
    rank_svg = rank_svg.replace('role="img"', 'role="img"', 1).replace(
        '><path', '><title>同一起点下的口令频次分布比较</title><path', 1)
    def attack_panel(source, conditional_curve, title):
        series = [
            (labels[0], [(point['budget'], point['rate']) for point in source[0]['minauto']]),
            (labels[1], [(point['budget'], point['rate']) for point in source[1]['minauto']]),
            (labels[2], [(point['budget'], point['rate']) for point in conditional_curve]),
        ]
        if len({tuple(x for x, _ in points) for _, points in series}) != 1:
            raise ValueError('attack curves do not have equal guess budgets')
        return svg_only(series, xlabel='每个攻击模型的猜测次数',
                        ylabel=title, log=True, x_format='power10',
                        y_format='percent', markers=True, y_domain=(0, .20)).strip()
    f_panel = attack_panel((initial['risk'], current['risk']),
                           conditional['F_curves']['union'], '固定 F 联合命中率')
    if 'A1_curves' in conditional:
        a1_panel = attack_panel((arms['google_hold']['attacks']['A1'],
                                 arms['google_dynamic']['attacks']['A1']),
                                conditional['A1_curves']['union'], '独立参考 A1 联合命中率')
        f_panel = f_panel.replace('<svg ', '<svg x="0" y="0" width="1050" height="474" ', 1)
        a1_panel = a1_panel.replace('<svg ', '<svg x="0" y="474" width="1050" height="474" ', 1)
        attack_svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1050 948" '
                      'role="img" aria-label="固定 F 和独立参考 A1 联合攻击对比">'
                      '<title>固定 F 与 A1 联合攻击曲线</title>'
                      +f_panel+a1_panel+'</svg>\n')
    else:
        attack_svg = f_panel.replace('><path',
            '><title>同一起点下的固定 F 联合攻击命中率比较</title><path', 1)+'\n'
    outputs = {}
    for name, markup in (('conditional_distribution.svg', rank_svg),
                         ('conditional_attack.svg', attack_svg)):
        path = OUT/name
        path.write_text(markup, encoding='utf-8')
        outputs[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'size': path.stat().st_size}
    (OUT/'conditional_figures.json').write_text(json.dumps({
        'run_id': conditional['run_id'], 'source_sha256': hashlib.sha256(
            (OUT/'conditional_comparison.json').read_bytes()).hexdigest(),
        'interpretation': 'The HTPG curve uses disclosed replacements for missing author definitions.',
        'figures': outputs}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return outputs


if __name__ == '__main__':
    print(json.dumps(export_figures(), ensure_ascii=False, indent=2))
