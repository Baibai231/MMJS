"""Traceable paper audit and comparison with the published intervention study.

An unavailable HTPG endpoint is represented by null, never an unchanged/zero-
risk arm. Only Algorithm 1 is executable from raw counts without the missing
definitions. Algorithms 2/3 are executable on their explicitly provided inputs.
"""
from __future__ import annotations

from array import array
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from core.counted_corpus import audit_counted_frequencies
from core.htpg_paper import VERSION, MissingPaperDefinition, fit_and_classify

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'reports' / 'htpg_paper'
PAPER_DOI = '10.1109/TIFS.2022.3152357'

GAPS = [
    {'id': 'categorical_mean', 'source': 'III-D, III-F; Algorithm 3 lines 8-9',
     'description': 'LSD 结构与 SpecPlace 是类别值，正文没有给出计算平均值和绝对差的数值编码。',
     'blocks': ['raw_password_policy_generation'],
     'rejected_fallback': '不以众数距离、随意类别编号或向量范数代替绝对差。'},
    {'id': 'feature_recognizers', 'source': 'III-D',
     'description': 'Date 和 Keyboard 没有完整识别语法；多处特殊字符如何归入四种 SpecPlace 也未交代。',
     'blocks': ['raw_password_feature_sort'],
     'rejected_fallback': '现有启发式识别器仅属于历史实现，不标为作者实现。'},
    {'id': 'author_lexicons', 'source': 'III-D',
     'description': '情绪词以及中英文常见姓氏词表未提供，也未说明词匹配边界。',
     'blocks': ['raw_password_feature_sort'],
     'rejected_fallback': '现有 VADER/Census 替代词表不自动注入严格组。'},
    {'id': 'igr_weighting', 'source': 'III-E; Algorithm 2',
     'description': '没有说明 IGR 和头尾平均值按不同口令等权还是按账户频次加权。',
     'blocks': ['raw_password_feature_sort'],
     'rejected_fallback': '核心要求调用方显式传入权重；报告不任选一种作为论文事实。'},
    {'id': 'lowercase_policy', 'source': 'III-D; Algorithm 3; Table IV',
     'description': '九项排序特征包含 Lowercase，但表 IV 只列八种策略；其进入前 M 时如何处理没有说明。',
     'blocks': ['raw_password_policy_generation'],
     'rejected_fallback': '不删除该特征再补入排名靠后的特征。'},
    {'id': 'response_functions', 'source': 'IV-A.2; Table IV; IV-D',
     'description': '只具体给出随机追加 1980—2020 年份；其余修改函数、具体尾部结构选择及不足两条建议的处理未提供。',
     'blocks': ['end_to_end_iteration', 'attack_comparison'],
     'rejected_fallback': '不使用固定后缀、短语或自创修补函数生成论文终态。'},
    {'id': 'integer_transition', 'source': 'IV-D; Fig.12',
     'description': '频次不能被 4 整除时，50%／25%／25% 如何分配整数账户未说明。',
     'blocks': ['individual_account_iteration'],
     'rejected_fallback': '核心提供精确期望质量转移；不能把分数质量冒充实际账户数。'},
    {'id': 'degenerate_sort', 'source': 'III-E; Eq.4; Algorithm 2',
     'description': 'IV=0 时 IGR 未定义；IGR 并列时排序及冲突处理的自动模拟规则未说明。',
     'blocks': ['degenerate_feature_sort'],
     'rejected_fallback': 'IV=0 返回未定义，并列明确记录调用方特征顺序。'},
    {'id': 'original_experiment', 'source': 'IV-A; IV-B/C/D',
     'description': '论文使用六份跨站语料、PCFG 与 OMEN、最高 10^8 次猜测；项目当前共同起点、预算和账户响应不同。',
     'blocks': ['paper_figure_reproduction'],
     'rejected_fallback': '同项目协议的对照不能冒充论文 69% 改善或可用性调查复现。'},
]

LEGACY_DIFFERENCES = [
    {'file': 'policy/htpg_generator.py', 'difference': '默认 experimental_strict 使用 <；算法 3 使用 <=。'},
    {'file': 'policy/htpg_generator.py', 'difference': '用类别众数的 0/1 距离替代未定义的类别平均值绝对差。'},
    {'file': 'policy/htpg_generator.py', 'difference': '相同头尾均值直接不发建议；算法 3 第 10 行仍会通过相等距离。'},
    {'file': 'policy/htpg_generator.py', 'difference': 'Lowercase 不在表 IV，历史实现跳过后再计入 M，改变前 M 项语义。'},
    {'file': 'experiments/htpg_iteration.py', 'difference': '全体用户随机半数最多采用两项，而 IV-D 对每个头部频次分别按三条分支处理。'},
    {'file': 'experiments/htpg_iteration.py', 'difference': '合成语料与频次闭集排名不是论文六份跨站语料和 PCFG/OMEN 实验。'},
]


def readiness():
    return {'version': VERSION, 'paper_doi': PAPER_DOI,
            'full_reproduction': False,
            'status': 'missing_paper_definitions',
            'algorithm_contracts_implemented': ['Algorithm 1', 'Algorithm 2', 'Algorithm 3',
                                                'IV-D expected-mass transition'],
            'raw_password_end_to_end_ready': False, 'gaps': GAPS,
            'legacy_differences': LEGACY_DIFFERENCES}


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _current_report(run_id=None):
    root = ROOT / 'published' / 'intervention'
    if run_id is None:
        run_id = json.loads((root/'latest.json').read_text(encoding='utf-8'))['run_id']
    if not isinstance(run_id, str) or len(run_id) != 16 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('invalid published run id')
    path = root / run_id / 'report.json.gz'
    checksum = path.with_suffix(path.suffix+'.sha256')
    if not checksum.is_file() or checksum.read_text().split()[0] != _sha(path):
        raise ValueError('published report checksum mismatch')
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream), {'run_id': run_id, 'report_sha256': _sha(path)}


def _point(risk, budget, model=None):
    if risk is None:
        return None
    curve = risk if model is None else risk.get('attack_curves', {}).get(model, {})
    return next((r['rate'] for r in curve.get('minauto', []) if r['budget'] == budget), None)


def compare_current(report):
    budget = report['config']['risk_budget']
    rows = []
    for key in ('google_hold', 'google_dynamic', 'google_random', 'google_frozen'):
        arm = report['arms'][key]
        final, start = arm['final'], arm['trajectory'][0]
        ledger = final['ledger']
        rows.append({'key': key, 'label': arm['label'], 'status': 'measured_project_snapshot',
                     'users': ledger['accounts'], 'rounds': len(arm['rounds']),
                     'start_state_sha256': start['state_sha256'],
                     'start_account_state_sha256': start['account_state_sha256'],
                     'F_pcfg_rate': _point(final['risk'], budget, 'pcfg'),
                     'F_omen_rate': _point(final['risk'], budget, 'markov'),
                     'F_union_rate': _point(final['risk'], budget),
                     'A1_union_rate': _point(arm.get('attacks', {}).get('A1'), budget),
                     'collision_probability': final['distribution']['collision_probability'],
                     'unique_rate': final['distribution']['unique_rate'],
                     'ideal_distance': final.get('ideal_distance', {}).get('fitted'),
                     'additional_affected_rate': ledger['adaptive_affected_rate'],
                     'total_changed_rate': ledger['changed_rate'],
                     'modification_events': ledger['modification_events']})
    rows.append({'key': 'htpg_paper', 'label': '论文 HTPG · 严格组',
                 'status': 'not_run_missing_definitions',
                 'users': report['config']['data']['users'], 'rounds': None,
                 'start_state_sha256': rows[0]['start_state_sha256'],
                 'start_account_state_sha256': rows[0]['start_account_state_sha256'],
                 **{k: None for k in ('F_pcfg_rate', 'F_omen_rate', 'F_union_rate', 'A1_union_rate',
                                     'collision_probability', 'unique_rate', 'ideal_distance',
                                     'additional_affected_rate', 'total_changed_rate', 'modification_events')}})
    starts = {r['start_account_state_sha256'] for r in rows[:-1]}
    if len(starts) != 1:
        raise ValueError('project arms do not share the same account starting state')
    return {'rows': rows, 'budget_per_attacker': budget,
            'union_total_attempts_upper_bound': 2*budget,
            'paper_vs_current_effect_estimate': None,
            'paper_group_was_executed': False,
            'comparison_is_complete': False,
            'interpretation': '现有方法为已发布项目快照；严格 HTPG 终态未生成，不能比较优劣。',
            'project_protocol': report['metadata']['protocol']}


def build_audit(*, paper_path=None, corpus_path=None, run_id=None, progress=None):
    report, identity = _current_report(run_id)
    from experiments.cdf_fit_benchmark import counts_from_summary
    current_fits = {}
    for key in ('google_hold', 'google_dynamic'):
        frequencies = [int(v) for v in counts_from_summary(report['arms'][key]['final']['distribution'])]
        try:
            current_fits[key] = fit_and_classify(frequencies)
        except MissingPaperDefinition as exc:
            current_fits[key] = {'status': exc.code, 'message': str(exc)}
    hashes = report['metadata'].get('source_hashes', {})
    changed_sources = [p for p, value in hashes.items() if not (ROOT/p).is_file() or _sha(ROOT/p) != value]
    result = {'schema_version': VERSION+'-audit', 'readiness': readiness(),
              'current_comparison': compare_current(report), 'project_shape_classification': current_fits,
              'project_dataset': report['dataset'],
              'provenance': {**identity, 'snapshot_created_at': report['metadata']['created_at'],
                             'changed_project_sources_since_snapshot': changed_sources,
                             'paper_sha256': _sha(paper_path) if paper_path else None,
                             'core_sha256': _sha(ROOT/'core'/'htpg_paper.py'),
                             'implementation_sha256': {p: _sha(ROOT/p) for p in (
                                 'core/htpg_paper.py', 'experiments/htpg_paper_pipeline.py',
                                 'experiments/htpg_paper_audit.py', 'web/htpg_paper_interface.py')},
                             'created_at': datetime.now(timezone.utc).isoformat()},
              'corpus_reproduction': None, 'plaintext_retained': False}
    if corpus_path:
        if progress:
            progress('扫描带频次语料，核对论文 Table II/III；只保存汇总与哈希')
        audit = audit_counted_frequencies(corpus_path)
        frequencies = audit.pop('frequencies')
        # Sort counts only if file order is not descending; never sort plaintext.
        if audit['order_increases']:
            frequencies = array('Q', sorted(frequencies, reverse=True))
        if progress:
            progress('执行 f > 3 对数线性回归与解析曲率最大值')
        fit = fit_and_classify(frequencies)
        result['corpus_reproduction'] = {
            'audit': audit, 'fit': fit,
            'paper_table_II': {'C': 831682, 'alpha': .914},
            'paper_table_III': {'cutoff_rank': 1171, 'head_mass': 3899108, 'total_mass': 32510281},
            'local_vs_table': {'cutoff_rank_difference': fit['cutoff_rank']-1171,
                              'head_mass_difference': fit['head_mass']-3899108,
                              'total_mass_difference': audit['frequency_total']-32510281,
                              'C_difference': fit['C']-831682,
                              'alpha_difference': fit['alpha']-.914},
            'local_parsing': '按原始频次行解析，保留空字段和非 UTF-8 频次并分别审计；不等同项目抽样的 latin-1 非空口令清洗',
            'source_is_exact_author_dataset': False,
            'paper_boundary_was_hardcoded': False}
    return result


def save_audit(report, directory=OUTPUT):
    from web.htpg_paper_interface import render_audit
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')
    (directory/'report.json').write_bytes(raw)
    (directory/'report.json.sha256').write_text(hashlib.sha256(raw).hexdigest()+'  report.json\n', encoding='utf-8')
    (directory/'report.html').write_text(render_audit(report, document=True), encoding='utf-8')
    return directory
