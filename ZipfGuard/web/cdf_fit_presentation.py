"""CDF fit diagnostics using the workbenches' existing plot and legend style."""
from web.presentation import escape, plot, table


def fit_chart_specs(report, *, compare_legacy=False):
    specs = {}
    for row in report.get('distribution_fits', {}).get('results', []):
        if row['status'] != 'complete':
            continue
        curves = row['fit']['curves']
        entries = [('empirical', '实际累计分布')]
        if compare_legacy and row['fit'].get('previous_models'):
            entries.append(('zipf', '普通 Zipf 拟合'))
        entries.append(('cdf_sampling', 'CDF 采样拟合（5 次复核均值）'))
        series = [(label, [(p['rank'], p[key]) for p in curves]) for key, label in entries]
        specs['cdf_fit_'+row['key']] = (row['label']+' · 累计分布拟合', series,
            dict(xlabel='按频次排序的口令排名 r', ylabel='前 r 个口令覆盖的账户比例',
                 log=True, x_format='count', y_format='percent', y_domain=(0, 1), markers=False))
    return specs


def render_fit_diagnostics(report, *, compare_legacy=False):
    diagnostics = report.get('distribution_fits')
    if not diagnostics:
        return ''
    title = '普通 Zipf 与 CDF 采样优化' if compare_legacy else 'CDF 采样拟合'
    parts = ['<section id="distribution-fit"><h2>口令分布拟合：'+title+'</h2>',
             '<p>默认使用 CDF 采样方法拟合完整匿名频次。最大累计误差越小，曲线越贴近实际分布。'
             '图例沿用展示台的勾选、悬停与线型设计。</p>',
             '<p>这组曲线用于检验拟合质量；策略展示台的实际频次图和 PCFG 攻击结果保持原有含义。'
             '按频次排列的累计覆盖率不等于 PCFG 的猜中比例。</p>']
    if not diagnostics['policy_comparison_valid']:
        parts.append('<p class="notice">当前保存的 Google 对照仍是旧的局部试点版本。'
                     '本次仅对原始无政策账户拟合，不将旧试点作为全站 Google 起点。</p>')
    specs = fit_chart_specs(report, compare_legacy=compare_legacy)
    for row in diagnostics['results']:
        parts.append('<h3>'+escape(row['label'])+'</h3>')
        if row['status'] != 'complete':
            parts.append('<p>'+escape(row['reason'])+'</p>')
            continue
        fit = row['fit']
        new = fit['sampling_fit']
        parts.append(f'<p>{fit["users"]:,} 个账户，{fit["unique"]:,} 个不同口令。'
                     '误差均以累计覆盖率的百分点表示。</p>')
        rows = [[m['name'], f'{100*m["ks"]:.3f}', f'{m["runtime_seconds"]:.3f} 秒']
                for m in fit.get('previous_models', [])] if compare_legacy else []
        rows.append(['CDF 采样拟合（5 个独立种子平均）',
                     f'{100*new["replication"]["mean_max_cdf_error"]:.3f}',
                     f'{new["runtime_seconds"]:.3f} 秒'])
        parts.append(table(['拟合方法', '最大累计误差（百分点）', '拟合耗时'], rows))
        parts.append(f'<p>新方法独立复核中最差误差为 '
                     f'{100*new["replication"]["worst_max_cdf_error"]:.3f} 个百分点。'
                     '该复核使用同一份观测数据、不同模拟种子；耗时仅含拟合，不含复核。</p>')
        check = fit['heldout_shape_check']
        if check and compare_legacy:
            parts.append(f'<p>另用 {check["training_accounts"]:,} 个账户拟合，'
                         f'{check["validation_accounts"]:,} 个账户留出验证。'
                         '三种模型均模拟与验证集等大的样本，再按频次重新排名；各复核 5 次。</p>')
            parts.append(table(['留出验证：分布形状', '平均最大误差（百分点）'], [
                [label, f'{100*check[key]["mean_max_cdf_error"]:.3f}'] for key, label in (
                    ('zipf', '普通 Zipf'), ('cdf_zipf', '有限支持 CDF-Zipf'),
                    ('cdf_sampling', 'CDF 采样拟合'))]))
        name = 'cdf_fit_'+row['key']
        _, series, options = specs[name]
        parts.append(plot(series, distinguish=True, **options))
        parts.append(f'<p><a href="/api/intervention/figure/{report["metadata"]["run_id"]}/{name}.svg">下载带图例 SVG</a></p>')
        parts.append('<p>新模型描述的是样本频次形状；黄金分割搜索不保证全局最优。'
                     '拟合更好本身不能证明密码更安全。</p>')
    parts.append('</section>')
    return ''.join(parts)
