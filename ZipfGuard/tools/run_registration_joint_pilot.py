"""Small development experiment only. No Overleaf edits or final-test opening."""
from pathlib import Path
import sys, json, time, hashlib, csv
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import experiments.registration_joint_pipeline as joint
from experiments.registration_joint_data import load_private, save_private, developer_stream
from experiments.registration_comparison import simulate_arm, developer_counts, score_counts
from experiments.registration_joint_pilot import graded_candidates, ReusableAttackCache

OUT = ROOT / 'reports/registration_joint_pilot_20261011'
OUT.mkdir(parents=True, exist_ok=True)


def log(message):
    line = time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + message
    print(line, flush=True)
    with (OUT / 'events.log').open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def report(results, cfg):
    rows = []
    for arm, result in results.items():
        row = {'arm': arm, 'users': cfg['users'], 'seed': 11,
               'union_total_100k': result['actual_attacks']['union_equal_total_B']['points'][-1]['rate_per_initial_user'],
               'modification': result['cost']['modification_rate'],
               'extra': result['cost']['mean_extra_submissions'],
               'more_than_three': result['cost']['more_than_three_fraction'],
               'eligible': not result['eligibility_failures'],
               'whole_shape_batches': sum(t['shape_size'] > 0 for t in result['timeline']),
               'soft_shape_batches': sum(t.get('soft_shape_size', 0) > 0 for t in result['timeline'])}
        rows.append(row)
    with (OUT / 'results.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    names = {'joint': '原始联合方案', 'graded': '增加结构内限制', 'schechter': 'Schechter限频'}
    text = ['# 注册动态策略小规模探索结果', '',
            '每组6000名模拟用户，3批，相同用户、种子和重选序列；共享开发训练数据与基础规则。仅使用已有校准角色的子样本，属于探索，不是新的独立终测。', '',
            '原始方案沿用原控制器与完整候选网格；新方案保留原候选，并添加结构内弱口令限制和风险排序候选，其他选择、确认和成本检查一致。', '',
            '为缩短首轮时间，选择/确认预览各600人，蒙特卡洛选择3000、确认10000样本；风险目标及实际攻击预算降为10万。PCFG、OMEN分别生成10万次实际尝试，联合指标各取前5万次、总计10万次，重复输出计费。', '',
            'Schechter使用此前开发校准选出的阈值1e-5；未在本轮结果上重新挑选阈值。共同成本上限：修改比例60%、平均额外提交1次、额外提交超过3次的人数比例5%。相同上限不代表实际成本完全相同。', '',
            '| 方案 | 联合猜中比例 | 人均额外提交 | 修改比例 | 成本合格 | 整体结构/结构内限制批数 |',
            '|---|---:|---:|---:|---|---:|']
    for r in rows:
        text.append(f"| {names[r['arm']]} | {100*r['union_total_100k']:.3f}% | {r['extra']:.4f} | {100*r['modification']:.2f}% | {'是' if r['eligible'] else '否'} | {r['whole_shape_batches']}/{r['soft_shape_batches']} |")
    text += ['', '这轮只有一个种子，不能据此判断显著性、全面优于文献或完成新颖性证明。结构内限制是否被选中以及最终适应攻击下是否改善，分别报告。', '']
    (OUT / '小规模结果.md').write_text('\n'.join(text), 'utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for family in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC'):
        if any(f.name == family for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.sans-serif'] = [family]; break
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.8))
    labels = [names[r['arm']] for r in rows]
    for axis, key, title, scale in [(ax[0], 'union_total_100k', '总计10万次猜测：猜中比例（%）', 100),
                                     (ax[1], 'extra', '人均额外提交次数', 1)]:
        vals = [r[key]*scale for r in rows]
        axis.bar(labels, vals, color=['#8FA6BC', '#356A93', '#CF9A57'])
        axis.set_title(title); axis.spines[['top', 'right']].set_visible(False)
        axis.set_ylim(0, max(vals)*1.22)
        for i, v in enumerate(vals): axis.text(i, v+max(vals)*.025, f'{v:.3f}', ha='center')
    fig.suptitle('探索实验：6000名用户 × 1个配对种子')
    fig.tight_layout()
    fig.savefig(OUT / '小规模结果.png', dpi=180)
    fig.savefig(OUT / '小规模结果.pdf')
    plt.close(fig)
    return rows


def main():
    cfg = json.loads((ROOT / 'configs/registration_joint_minimal.json').read_text('utf-8'))
    cfg.update(users=6000, batches=3, preview_users=600, selection_samples=3000,
               confirmation_samples=10000, final_samples=10000, objective_budget=100000,
               budgets=[1000,10000,100000], frozen_budget=100000, cohort_aware=False,
               attack_timeout=600, cost_limits={'literature': {'cost_mode':'absolute',
               'max_modification':.6,'max_extra':1.,'max_more_than_three':.05}})
    identity = {'config':cfg, 'scope':'exploratory reused calibration subcohort; no test opening',
                'source_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                 [Path(__file__),ROOT/'experiments/registration_joint_pilot.py', ROOT/'experiments/registration_joint_pipeline.py']}}
    manifest = OUT / 'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text('utf-8')) != identity:
        raise RuntimeError('Pilot code/config changed; start a new output instead of silent resume')
    joint.write_json(manifest, identity)
    old = ROOT / 'reports/registration_comparison_final_20261011'
    data = load_private(old / 'private/partition.pkl.gz')
    records = data['roles']['calibration_11'][:cfg['users']]
    cohorts = [records[i:i+2000] for i in range(0,len(records),2000)]
    dev = developer_stream(data['roles']['train'], 'raw', 11, cfg['batches'], data['median_length'])
    cache = ReusableAttackCache(joint.AttackCache(OUT / 'private/attacks', cfg, log),
                               [old / 'private/attacks/literature/11'])
    raw_train = Counter(w for _,w in data['roles']['train'])
    frozen = cache.raw(raw_train, cfg['frozen_budget'], 'pcfg')
    # Only needed by legacy result diagnostics; no strength candidates in pilot.
    gate = cache.index(raw_train, cfg['final_samples'], 'frozen')
    prediction_counts = load_private(old / 'private/predictor-literature-11.pkl.gz')
    selection_prefixes = {m:cache.raw(prediction_counts,max(cfg['budgets']),m) for m in ('pcfg','omen')}
    guessed = frozenset(w for p in selection_prefixes.values() for w,r in p['ranks'].items()
                        if r <= cfg['objective_budget']//2)
    assert frozen['complete'] and all(p['complete'] for p in selection_prefixes.values())
    results = {}
    original = joint.candidates
    for arm in ('joint','graded','schechter'):
        joint.write_json(OUT / 'status.json', {'stage':arm,'completed':list(results),'updated_at':time.strftime('%Y-%m-%d %H:%M:%S')})
        log('Starting '+arm)
        if arm != 'schechter':
            joint.candidates = (original if arm == 'joint' else
                                lambda base,recent,hold,ks,js:graded_candidates(base,recent,hold,ks,js,guessed))
            # Keep original select_rule dispatch intact; distinguish outputs by directory.
            result = joint.trajectory('joint',cohorts,dev,data,cfg,11,{'fixed_pair':[1000,0]},
                                      OUT / arm,cache,frozen,gate,log)
            result['arm'] = arm
            joint.candidates = original
            if result['attack_failures']: raise RuntimeError(result['attack_failures'])
        else:
            path = OUT / 'schechter'
            saved = path / 'result.json'
            if saved.exists(): result=json.loads(saved.read_text('utf-8'))
            else:
                grammar,_ = cache.grammar(prediction_counts)
                state = simulate_arm('schechter',cohorts,data,cfg,'literature',11,{'rate':1e-5},path,
                                     selection_prefixes,grammar,log)
                train = developer_counts(state,'schechter',data,cfg,'literature',11,{'rate':1e-5})
                prefixes = {m:cache.raw(train,max(cfg['budgets']),m) for m in ('pcfg','omen')}
                if not all(p['complete'] for p in prefixes.values()): raise RuntimeError('Incomplete attack')
                cost = joint.summed_cost(state['costs'])
                result = {'arm':arm,'seed':11,'status':'complete','cost':cost,'timeline':state['timeline'],
                          'eligibility_failures':joint.cost_failures(cost,cfg),'actual_attacks':{
                          'union_equal_total_B':{'points':[{'budget':b,'rate_per_initial_user':r}
                            for b,r in zip(cfg['budgets'],score_counts(state['history'],prefixes,cfg['budgets'],cfg['users']))]}}}
                joint.write_json(saved,result)
        results[arm]=result
        joint.write_json(OUT / 'summary.json', results)
        log('Finished '+arm)
    rows=report(results,cfg)
    joint.write_json(OUT / 'status.json', {'stage':'complete','completed':list(results),'rows':rows,
                                         'completed_at':time.strftime('%Y-%m-%d %H:%M:%S')})
    log('Pilot complete; results and figure saved')


if __name__=='__main__':
    try: main()
    except Exception as exc:
        import traceback
        joint.write_json(OUT / 'status.json', {'stage':'failed','error':str(exc),'traceback':traceback.format_exc()})
        raise
