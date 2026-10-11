"""Recount completed pilot outcomes; no additional experiment trajectories."""
from pathlib import Path
from collections import Counter
import sys, json, hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.registration_joint_data import load_private
from experiments.registration_comparison import developer_counts, score_counts
from experiments.registration_joint_pipeline import AttackCache, write_json
from core.corpus import counts_hash

out = ROOT / 'reports/registration_joint_pilot_20261011'
cfg = json.loads((out / 'manifest.json').read_text('utf-8'))['config']
data = load_private(ROOT / 'reports/registration_comparison_final_20261011/private/partition.pkl.gz')
summary = json.loads((out / 'summary.json').read_text('utf-8'))
records = data['roles']['calibration_11'][:6000]
target_ids = {i for i,_ in records}
assert all(not target_ids.intersection(i for i,_ in data['roles'][role])
           for role in ('train','preview_select','preview_confirm'))
cache = AttackCache(out / 'private/attacks', cfg, print)
rows = []
for arm, result in summary.items():
    checkpoint = out / arm / ('joint/state.pkl.gz' if arm != 'schechter' else 'state.pkl.gz')
    state = load_private(checkpoint)
    history = state['history']
    training = (state['dev_history'] if arm != 'schechter' else
                developer_counts(state,arm,data,cfg,'literature',11,{'rate':1e-5}))
    key = counts_hash(training)
    paths = {m:out / 'private/attacks/prefixes' / f'{key}-{m}-100000.pkl.gz'
             for m in ('pcfg','omen')}
    assert all(p.is_file() for p in paths.values())
    prefixes = {m:load_private(p) for m,p in paths.items()}
    assert all(p['complete'] and p['raw_emissions']==100000 for p in prefixes.values())
    rates = score_counts(history,prefixes,cfg['budgets'],6000)
    recorded = result['actual_attacks']['union_equal_total_B']['points']
    assert all(abs(a-b['rate_per_initial_user']) < 1e-12 for a,b in zip(rates,recorded))
    assert sum(history.values()) == 6000 and result['cost']['pending_users'] == 0
    assert result['cost']['common_base_mean_extra_submissions'] == summary['joint']['cost']['common_base_mean_extra_submissions']
    grammar,_ = cache.grammar(training)
    meta = prefixes['omen']['metadata']
    alphabet = set(meta['alphabet'])
    def supported(w):
        return grammar.probability(w)>0 or (meta['min_length']<=len(w)<=meta['max_length'] and all(c in alphabet for c in w))
    outside_both = sum(n for w,n in history.items() if not supported(w))/6000
    rows.append({'arm':arm,'union_hits':round(rates[-1]*6000),'initial_users':6000,
                 'training_occurrences':sum(training.values()),'training_counts_sha256':key,
                 'pcfg_100k_hits':sum(n for w,n in history.items() if w in prefixes['pcfg']['ranks']),
                 'omen_100k_hits':sum(n for w,n in history.items() if w in prefixes['omen']['ranks']),
                 'outside_both_model_domains':outside_both,
                 'prefix_file_sha256':{m:hashlib.sha256(p.read_bytes()).hexdigest() for m,p in paths.items()}})
audit = {'status':'passed','same_target_occurrences':True,'same_base_cost':True,
         'all_complete':True,'union_recount_matches':True,'rows':rows,
         'target_occurrence_id_sha256':hashlib.sha256(','.join(map(str,sorted(target_ids))).encode()).hexdigest(),
         'interpretation':'One reused calibration subcohort; exploratory, not independent final validation.'}
write_json(out / 'audit.json',audit)
new = summary['graded']['actual_attacks']['union_equal_total_B']['points'][-1]['rate_per_initial_user']
old = summary['joint']['actual_attacks']['union_equal_total_B']['points'][-1]['rate_per_initial_user']
sch = summary['schechter']['actual_attacks']['union_equal_total_B']['points'][-1]['rate_per_initial_user']
extra_new = summary['graded']['cost']['mean_extra_submissions']
extra_old = summary['joint']['cost']['mean_extra_submissions']
extra_sch = summary['schechter']['cost']['mean_extra_submissions']
text = '\n'.join(['', '## 结果判断', '',
    f'新增方案相对原始联合方案，联合猜中比例下降{100*(old-new):.3f}个百分点（相对下降{100*(old-new)/old:.2f}%），人均额外提交增加{extra_new-extra_old:.4f}次。', '',
    f'相对Schechter限频，联合猜中比例下降{100*(sch-new):.3f}个百分点（相对下降{100*(sch-new)/sch:.2f}%），人均额外提交增加{extra_new-extra_sch:.4f}次（相对增加{100*(extra_new-extra_sch)/extra_sch:.2f}%）。三组均满足共同上限，但新方案不是在完全相同的实际成本下取得改善。', '',
    '原始方案没有采用整体结构限制；新方案第三批采用20个结构的内部弱口令限制，没有禁止整个结构。第二批新候选因确认时PCFG评价范围外比例增量超限而保持基础规则，第三批才确认更新。', '',
    '这轮支持继续研究“保留结构、只限制其中容易猜中的口令”的方向；尚不能证明结构内限制单独贡献了全部改善，因为新候选同时包含风险排序。这轮也不能证明相同实际成本下优于文献。', '',
    '| 方案 | 超出PCFG与OMEN共同评价范围的比例 |', '|---|---:|']+
    [f"| {r['arm']} | {100*r['outside_both_model_domains']:.3f}% |" for r in rows]+['',
    '核验已从原始猜测前缀重新计数：两攻击器均完成10万次尝试，联合结果各取5万次；每组6000人全部完成，攻击模型仅使用6万个开发训练出现记录。', ''])
note = out / '小规模结果.md'
source = note.read_text('utf-8').split('\n## 结果判断')[0]
note.write_text(source+text,'utf-8')
print(json.dumps({'audit':'passed','rows':rows,'new_vs_original_pp':100*(old-new),'new_vs_schechter_pp':100*(sch-new)},ensure_ascii=False))
