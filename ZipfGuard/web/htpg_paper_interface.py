"""Strict HTPG surface inside the existing intervention workbench."""
from html import escape
import hashlib
import json

from core.htpg_paper import (MissingPaperDefinition, fit_and_classify,
                             feature_sort, policies_generation)
from experiments.htpg_paper_audit import OUTPUT, readiness
from web.presentation import STYLE

GAP_LABELS = {
    'categorical_mean': '类别特征的平均值',
    'feature_recognizers': '特征识别规则',
    'author_lexicons': '作者词表',
    'igr_weighting': '特征统计权重',
    'lowercase_policy': 'Lowercase 对应策略',
    'response_functions': '口令修改函数',
    'integer_transition': '整数账户分配',
    'degenerate_sort': '退化情况与并列排序',
    'original_experiment': '原始实验条件',
}


def latest_audit():
    path = OUTPUT/'report.json'
    if not path.is_file():
        return {'report': None, 'readiness': readiness()}
    raw = path.read_bytes()
    checksum = path.with_suffix('.json.sha256')
    if not checksum.is_file() or checksum.read_text().split()[0] != hashlib.sha256(raw).hexdigest():
        raise ValueError('HTPG 核验报告校验和不一致')
    return {'report': json.loads(raw), 'readiness': readiness(),
            'conditional': latest_conditional()}


def latest_conditional():
    path = OUTPUT/'conditional_comparison.json'
    if not path.is_file():
        return None
    raw = path.read_bytes()
    checksum = path.with_suffix('.json.sha256')
    if not checksum.is_file() or checksum.read_text().split()[0] != hashlib.sha256(raw).hexdigest():
        raise ValueError('HTPG 条件实验校验和不一致')
    return json.loads(raw)


def latest_figure(name):
    if name not in ('conditional_distribution.svg', 'conditional_attack.svg'):
        raise ValueError('unknown HTPG figure')
    manifest_path = OUTPUT/'conditional_figures.json'
    path = OUTPUT/name
    if not manifest_path.is_file() or not path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    raw = path.read_bytes()
    conditional = OUTPUT/'conditional_comparison.json'
    if (manifest['figures'][name]['sha256'] != hashlib.sha256(raw).hexdigest()
            or manifest['source_sha256'] != hashlib.sha256(conditional.read_bytes()).hexdigest()):
        raise ValueError('HTPG 图表与条件实验结果不一致')
    return raw


def _percent(value):
    return '未运行' if value is None else f'{value:.3%}'


def render_audit(report=None, *, document=False):
    if report is None:
        report = latest_audit()['report']
    ready = readiness() if report is None else report['readiness']
    conditional = latest_conditional()
    body = '<main><section id="htpg-paper"><h1>论文 HTPG：严格复现核验</h1>'
    body += '<style>.htpg-figure svg{display:block;width:100%;min-width:0;max-height:none}</style>'
    body += ('<p>计算核心对应 Zipf 曲率分类、IGR 特征排序、逐口令策略生成和每轮重新计算。'
             '正文缺失的原始定义列在下方；使用公开补全假设运行的条件组单独展示，不能视为作者原版结果。</p>'
             '<p><a href="/intervention">返回项目实验台</a> · '
             '<a href="https://doi.org/10.1109/TIFS.2022.3152357">查看论文</a> · '
             '<a href="/api/intervention/htpg/report">下载核验报告</a> · '
             '<a href="/api/intervention/htpg/conditional">下载条件组结果</a></p>')
    if report:
        comparison = report['current_comparison']
        body += '<h2>与当前项目方法的对比状态</h2>'
        body += ('<p>现有方法来自已发布的 '+escape(report['provenance']['run_id'])+' 运行快照；'
                 f'每个攻击器最多 {comparison["budget_per_attacker"]:,} 次尝试，联合攻击最多 '
                 f'{comparison["union_total_attempts_upper_bound"]:,} 次。严格组未运行的数值留空。</p>')
        body += ('<div style="overflow-x:auto"><table><thead><tr><th>方法</th><th>轮数</th>'
                 '<th>PCFG F 命中率</th><th>OMEN F 命中率</th><th>联合 F 命中率</th>'
                 '<th>联合 A1 命中率</th><th>后续通知比例</th><th>碰撞概率</th></tr></thead><tbody>')
        for row in comparison['rows']:
            collision = '未运行' if row['collision_probability'] is None else f'{row["collision_probability"]:.6g}'
            rounds = '未运行' if row['rounds'] is None else str(row['rounds'])
            body += ('<tr><th>'+escape(row['label'])+'</th><td>'+rounds+'</td>'
                     +''.join('<td>'+_percent(row[key])+'</td>' for key in
                              ('F_pcfg_rate', 'F_omen_rate', 'F_union_rate', 'A1_union_rate', 'additional_affected_rate'))
                     +'<td>'+collision+'</td></tr>')
        if conditional and conditional['run_id'] == report['provenance']['run_id']:
            metrics = conditional['metrics']
            body += ('<tr><th>HTPG 正文算法＋补全假设</th><td>'+str(len(conditional['rounds']))+'</td>'
                     +''.join('<td>'+_percent(metrics[key])+'</td>' for key in
                              ('F_pcfg_rate', 'F_omen_rate', 'F_union_rate'))
                     +'<td>'+_percent(metrics.get('A1_union_rate'))+'</td>'
                     +'<td>期望质量，见报告</td><td>'
                     +f'{conditional["rounds"][-1]["hhi_after"]:.6g}</td></tr>')
        body += '</tbody></table></div><p>窄屏表格可左右滑动查看。严格组仍缺作者原始定义；条件组只反映所列补全假设。</p>'
        if conditional and conditional['run_id'] == report['provenance']['run_id']:
            body += ('<p>条件组与当前方法使用相同的 100,000 账户起点和固定 F 攻击模型。'
                     f'联合 F 命中率差值（条件组－当前方法）：{conditional["F_union_delta_vs_current"]:+.3%}。'
                     '条件组按期望频次质量转移。</p>')
            if conditional['metrics'].get('A1_union_rate') is not None:
                body += (f'<p>独立参考人群重训后的 A1 联合命中率为 '
                         f'{conditional["metrics"]["A1_union_rate"]:.3%}；'
                         f'相对当前方法差值为 {conditional["A1_union_delta_vs_current"]:+.3%}。'
                         '参考人群用固定种子的整数账户 50%／25%／25% 抽样，目标组仍按论文期望质量转移；'
                         '该抽样规则属于明确补全假设。</p>')
            distribution_figure = latest_figure('conditional_distribution.svg')
            if distribution_figure is not None:
                body += ('<h2>调整后的口令频次分布</h2><p>同一账户起点、当前动态方法和 HTPG 条件组；'
                         '横纵轴为对数刻度，条件组的分数频次表示期望质量。</p>'
                         '<div class="htpg-figure" style="width:100%;max-width:1050px">'
                         +distribution_figure.decode('utf-8')+'</div>'
                         '<p><a href="/api/intervention/htpg/conditional_distribution.svg">单独打开分布图</a></p>')
            attack_figure = latest_figure('conditional_attack.svg')
            if attack_figure is not None:
                title = '固定 F 与 A1 联合攻击' if 'A1_curves' in conditional else '固定 F 联合攻击'
                note = ('A1 在独立参考人群终态重训。' if 'A1_curves' in conditional else 'A1 尚未运行。')
                body += ('<h2>'+title+'</h2><p>每个攻击模型独立使用横轴所示的原始猜测次数；'
                         '两模型命中同一口令时只计一次。'+note+'</p>'
                         '<div class="htpg-figure" style="width:100%;max-width:1050px">'
                         +attack_figure.decode('utf-8')+'</div>'
                         '<p><a href="/api/intervention/htpg/conditional_attack.svg">单独打开攻击图</a></p>')
        changed = report['provenance']['changed_project_sources_since_snapshot']
        if changed:
            body += '<p>运行后已有源码变化，因此表格代表保存的运行快照；当前代码需要重新运行才能更新结论。</p>'
        body += '<h2>在项目分布上执行论文分类器</h2><ul>'
        for key, fit in report['project_shape_classification'].items():
            label = '共同 8 字符起点' if key == 'google_hold' else '当前动态方法终态'
            if 'alpha' in fit:
                body += f'<li><strong>{label}：</strong> alpha={fit["alpha"]:.6f}，头部截至第 {fit["cutoff_rank"]} 名，占账户的 {fit["head_mass_rate"]:.3%}。</li>'
            else:
                body += '<li><strong>'+label+'：</strong> '+escape(fit['message'])+'</li>'
        body += '</ul><p>这些是分类诊断，不是运行 HTPG 后的安全收益。</p>'
        corpus = report.get('corpus_reproduction')
        if corpus:
            fit, local = corpus['fit'], corpus['audit']
            body += ('<h2>本地完整 RockYou 频次核验</h2>'
                     f'<p>有效频次记录 {local["valid_rows"]:,}；总质量 {local["frequency_total"]:,}；'
                     f'C={fit["C"]:.6f}，alpha={fit["alpha"]:.9f}；'
                     f'连续曲率峰 {fit["curvature_x0"]:.6f}，整数头部截至 {fit["cutoff_rank"]}，'
                     f'头部质量 {fit["head_mass"]:,.0f}。</p>'
                     '<p>论文 Table III 的 RockYou 头部名次为 1171，总账户数为 32,510,281。'
                     '本地版本和清洗口径存在差异，核验报告保留差值，没有强制改写成论文结果。</p>')
    body += '<h2>正文缺口</h2><ul>'
    for gap in ready['gaps']:
        body += '<li><strong>'+escape(GAP_LABELS[gap['id']])+'</strong> · '+escape(gap['source'])+'：'+escape(gap['description'])+'</li>'
    body += '</ul><h2>已实现的算法行为</h2><ul>'
    body += ('<li>仅 f &gt; 3 进入对数线性回归，以解析曲率最大值分类；所有低频口令仍保留。</li>'
             '<li>算法 2 计算 IGR，调用方必须显式提供样本权重；零分母保持未定义。</li>'
             '<li>算法 3 只检查排序后的前 M 项，距离相等也通过；尾部口令返回空建议。</li>'
             '<li>每个头部口令按 50% 保持、25% 第一项、25% 第二项转移；允许后续轮次再次修改。</li>'
             '<li>缺少数值编码或响应函数时停止执行，错误状态不会计为零风险。</li>')
    body += '</ul></section></main>'
    if document:
        return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HTPG 严格复现核验</title><style>'+STYLE+'</style><body>'+body+'</body></html>'
    return body


def execute_contract(request):
    if not isinstance(request, dict):
        raise ValueError('请求必须为 JSON 对象')
    operation = request.get('operation')
    if operation == 'run':
        return {'status': 'not_run_missing_definitions', 'readiness': readiness(),
                'paper_group_was_executed': False, 'paper_result': None}, 409
    try:
        if operation == 'fit':
            frequencies = request['frequencies']
            if not isinstance(frequencies, list) or not 1 <= len(frequencies) <= 200000:
                raise ValueError('频次向量需要 1—200000 个元素')
            result = fit_and_classify(frequencies)
        elif operation == 'feature_sort':
            rows = request['rows']
            if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
                raise ValueError('特征表需要 1—10000 行')
            result = feature_sort(rows, request['labels'], weights=request['weights'])
        elif operation == 'policies':
            ordered = request['ordered_features']
            if not isinstance(ordered, list) or len(ordered) > 100:
                raise ValueError('特征顺序无效')
            result = policies_generation(in_head=request['in_head'], ordered_features=ordered,
                pwd_features=request['pwd_features'], head_features=request['head_features'],
                tail_features=request['tail_features'], policies=request['policies'], m=request['m'])
        else:
            raise ValueError('operation 应为 fit、feature_sort、policies 或 run')
    except MissingPaperDefinition as exc:
        return {'status': 'missing_paper_definition', 'code': exc.code,
                'error': str(exc), 'result': None, 'full_reproduction': False}, 422
    return {'status': 'computed_on_supplied_inputs', 'result': result,
            'full_reproduction': False,
            'scope': '算法输入由调用方提供；不代表原始特征定义与论文全实验复现'}, 200
