"""Per-round CDF sampling parameters: line style identifies parameter, color identifies round."""
import math

from web.presentation import escape


ROUND_COLORS = ('#2563eb', '#e16b24', '#189b80', '#a855b5', '#cc4255',
                '#667a15', '#087d99', '#8d6849', '#374151', '#db2777', '#ca8a04')


def round_parameter_svg(rows, *, requested_rounds=10):
    if not rows:
        return ''
    if [row['round'] for row in rows] != list(range(len(rows))):
        raise ValueError('逐轮参数必须从共同起点连续编号')
    if len(rows) > len(ROUND_COLORS):
        raise ValueError('轮次颜色不足')
    x_max = max(1, requested_rounds, rows[-1]['round'])
    x_at = lambda rnd: 95 + 745 * rnd / x_max
    pieces = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 610" '
              'role="img" aria-label="共同 8 字符基础规则 起点及逐轮 CDF 采样拟合参数 c 和 s">',
              '<style>text{font-family:system-ui,sans-serif;fill:#20304a}'
              '.tick{font-size:11px;fill:#607087}.caption{font-size:13px}</style>']
    for key, label, top, dash in [('c', '参数 c（实线）', 65, ''),
                                   ('s', '参数 s（虚线）', 270, '8 5')]:
        vals = [row['parameters'][key] for row in rows]
        if any(not math.isfinite(v) or v <= 0 for v in vals):
            raise ValueError('逐轮拟合参数无效')
        low, high = min(vals), max(vals)
        pad = max((high-low)*.14, high*.002, 1e-7)
        low, high = max(0., low-pad), high+pad
        bottom = top+150
        y_at = lambda value: bottom-150*(value-low)/(high-low)
        pieces.append(f'<text x="95" y="{top-22}" class="caption">{escape(label)}</text>')
        for step in range(5):
            value = low+(high-low)*step/4
            y = y_at(value)
            pieces.append(f'<path d="M95 {y:.2f}H840" stroke="#e5ebf3"/>'
                          f'<text x="86" y="{y+4:.2f}" text-anchor="end" class="tick">{value:.6f}</text>')
        pieces.append(f'<g class="parameter-{key}">')
        for left, right in zip(rows, rows[1:]):
            color = ROUND_COLORS[right['round']]
            dash_attr = f' stroke-dasharray="{dash}"' if dash else ''
            x1, y1 = x_at(left['round']), y_at(left['parameters'][key])
            x2, y2 = x_at(right['round']), y_at(right['parameters'][key])
            if dash:
                # Explicit subpaths keep the dashed distinction in SVG renderers
                # that do not honor stroke-dasharray on colored short segments.
                distance = math.hypot(x2-x1, y2-y1)
                strokes = []
                for offset in range(0, math.ceil(distance), 14):
                    end = min(offset+8, distance)
                    if end <= offset:
                        continue
                    a, b = offset/distance, end/distance
                    strokes.append(f'M{x1+(x2-x1)*a:.2f} {y1+(y2-y1)*a:.2f}'
                                   f'L{x1+(x2-x1)*b:.2f} {y1+(y2-y1)*b:.2f}')
                geometry = ' '.join(strokes)
            else:
                geometry = f'M{x1:.2f} {y1:.2f}L{x2:.2f} {y2:.2f}'
            pieces.append(f'<path d="{geometry}"'
                          f' fill="none" stroke="{color}" stroke-width="2.7"{dash_attr}/>')
        for row in rows:
            rnd, value = row['round'], row['parameters'][key]
            title = f'第 {rnd} 轮；{key}={value:.8g}；平均最大 CDF 误差='
            error = row.get('mean_max_cdf_error')
            title += '未记录' if error is None else f'{error:.4%}'
            pieces.append(f'<circle cx="{x_at(rnd):.2f}" cy="{y_at(value):.2f}" r="5"'
                          f' fill="{ROUND_COLORS[rnd]}" stroke="white" stroke-width="1.5">'
                          f'<title>{escape(title)}</title></circle>')
        pieces.append('</g>')
    for rnd in range(x_max+1):
        x = x_at(rnd)
        pieces.append(f'<text x="{x:.2f}" y="446" text-anchor="middle" class="tick">{rnd}</text>')
    pieces.append('<text x="465" y="468" text-anchor="middle" class="caption">8 字符基础规则 起点后的动态调整轮次（0 = 共同起点）</text>')
    pieces.append('<g aria-label="参数线型图例" class="caption">'
                  '<path d="M95 493H130" stroke="#475569" stroke-width="2.7"/>'
                  '<text x="139" y="498">c：实线</text>'
                  '<path d="M270 493H278 M284 493H292 M298 493H305" stroke="#475569" stroke-width="2.7"/>'
                  '<text x="314" y="498">s：虚线</text>'
                  '<text x="440" y="498">圆点及通向该点的线段颜色表示轮次</text></g>')
    for row in rows:
        rnd = row['round']
        col, line = rnd % 6, rnd // 6
        x, y = 103+133*col, 530+27*line
        label = '0 · 8 字符基础规则 起点' if rnd == 0 else f'{rnd} · 调整后'
        pieces.append(f'<g aria-label="{escape(label)}"><circle cx="{x}" cy="{y-4}" r="5"'
                      f' fill="{ROUND_COLORS[rnd]}"/><text x="{x+11}" y="{y}" font-size="12">'
                      f'{escape(label)}</text></g>')
    pieces.append('</svg>')
    return ''.join(pieces)


def round_parameter_html(rows, *, requested_rounds=10):
    if not rows:
        return '<p>没有逐轮拟合参数。</p>'
    svg = round_parameter_svg(rows, requested_rounds=requested_rounds)
    return ('<div class="round-parameters"><style>'
            '.round-parameters>svg{max-height:620px;min-width:760px}'
            '.round-parameters>.legend>label{flex-direction:row;align-items:center;gap:6px;margin:0;cursor:pointer}'
            '.round-parameters:has(>.legend>.param-c input:not(:checked))>svg .parameter-c{display:none}'
            '.round-parameters:has(>.legend>.param-s input:not(:checked))>svg .parameter-s{display:none}'
            '</style><div class="legend">'
            '<label class="param-c"><input type="checkbox" checked aria-label="显示参数 c">'
            '<span style="font-weight:700">━</span> 参数 c（实线）</label>'
            '<label class="param-s"><input type="checkbox" checked aria-label="显示参数 s">'
            '<span style="font-weight:700">┄</span> 参数 s（虚线）</label>'
            '</div>'+svg+'</div>')
