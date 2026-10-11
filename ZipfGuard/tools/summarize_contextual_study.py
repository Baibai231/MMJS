"""Summarize completed paired runs only; never select hyperparameters here."""
import argparse
import json
from pathlib import Path
from statistics import mean

LABELS={'heuristic':'经验优先搜索','random':'随机候选搜索','greedy':'学习但不探索',
        'bandit':'上下文老虎机','legacy':'原控制器参考'}


def summarize(paths):
    reports=[]
    for path in paths:
        report=json.loads(path.read_text(encoding='utf-8'))
        if report.get('status')!='complete':
            raise ValueError('Incomplete result: '+str(path))
        reports.append((path,report))
    text=['# 新旧动态策略配对实验结果','',
          '**范围:** 真实带频次语料的离线抽样，修改行为为模拟；每模型使用一百万次实际尝试，联合最多二百万次。',
          '', '**比较口径:** 前四组共享每轮候选和模拟次数上限；原控制器使用原拟合评分与搜索机制，计算开销不封顶，仅作参考。预热费用单列。相同通知上限不等于实际通知人数相同。','']
    for path,r in reports:
        n=r['configuration']['intervention']['data']['users']
        seed=r['configuration']['intervention']['seed']
        text += [f'## {n:,} 个账户，种子 {seed}','',
                 f'共同起点经验距离：{r["start_distance"]:.9f}。', '',
                 '| 方法 | 终态经验距离↓ | 实际通知人数↓ | 固定 F 联合命中率↓ | 适应性 A1 联合命中率↓ | 控制环节秒数 | 响应调用次数 |',
                 '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
        for method,a in r['arms'].items():
            a1=f'{a["A1"]["minauto"][-1]["rate"]:.3%}' if a['A1'] else '未运行'
            text.append(f'| {LABELS[method]} | {a["final_distance"]:.9f} | {a["ledger"]["adaptive_affected"]} | '
                        f'{a["F"]["minauto"][-1]["rate"]:.3%} | {a1} | {a["controller_seconds"]:.2f} | {a["response_calls"]} |')
        if r['warmup']:
            w=r['warmup']
            text += ['',f'学习预热：{w["learning_observations"]} 个执行反馈，{w["response_calls"]} 次响应调用，耗时 {w["controller_seconds"]:.2f} 秒；训练费用不包含在表中各方法的控制时间内。']
        text += ['', '控制时间不含最终 F/A1 模型训练；包含各自的群体选择、响应与参考群体重放，且受本机负载影响。', '',
                 f'原始证据：[report.json]({path.resolve().as_posix()})。','']
    sizes=sorted({r['configuration']['intervention']['data']['users'] for _,r in reports})
    text += ['## 按账户规模汇总','',
             '下表为不同种子的算术平均；种子数少时只作探索性描述，不作统计显著性或跨语料泛化声明。','',
             '| 账户数 | 运行数 | 方法 | 平均距离↓ | 平均通知人数↓ | 平均 A1 命中率↓ |',
             '| ---: | ---: | --- | ---: | ---: | ---: |']
    for n in sizes:
        group=[r for _,r in reports if r['configuration']['intervention']['data']['users']==n]
        common=set.intersection(*(set(r['arms']) for r in group))
        for method in LABELS:
            if method not in common:continue
            aa=[r['arms'][method] for r in group]
            a1=f'{mean(a["A1"]["minauto"][-1]["rate"] for a in aa):.3%}' if all(a['A1'] for a in aa) else '未全部运行'
            text.append(f'| {n} | {len(group)} | {LABELS[method]} | {mean(a["final_distance"] for a in aa):.9f} | '
                        f'{mean(a["ledger"]["adaptive_affected"] for a in aa):.2f} | {a1} |')
    text += ['', '## 新老虎机与原控制器的配对差值','',
             '差值均为“新老虎机减去原控制器”；距离、人数、命中率均越小越好。A1 差值单位为百分点。','',
             '| 账户数 | 种子 | 距离差 | 通知人数差 | A1 命中率差（百分点） |',
             '| ---: | ---: | ---: | ---: | ---: |']
    for _,r in reports:
        if not {'bandit','legacy'}<=set(r['arms']):continue
        a,b=r['arms']['bandit'],r['arms']['legacy']
        delta=(f'{100*(a["A1"]["minauto"][-1]["rate"]-b["A1"]["minauto"][-1]["rate"]):+.3f}'
               if a['A1'] and b['A1'] else '未运行')
        text.append(f'| {r["configuration"]["intervention"]["data"]["users"]} | '
                    f'{r["configuration"]["intervention"]["seed"]} | '
                    f'{a["final_distance"]-b["final_distance"]:+.9f} | '
                    f'{a["ledger"]["adaptive_affected"]-b["ledger"]["adaptive_affected"]:+d} | {delta} |')
    text += ['','## 解释限制','',
             '- 原控制器和新控制器的目标及搜索资源不同，不能把两者全部差异归因于老虎机。',
             '- 分布达到单例状态时主目标已饱和，攻击风险仍可能存在；不能将零距离称为零攻击风险。',
             '- 实际通知人数不同的 A1 终态排名，是各自停止策略的结果，不是严格等实际修改人数的安全性对照。',
             '- 学习与无探索组使用同一预热模型；若效果一致，则未显示探索的额外收益。',
             '- 当前为经验准入模式，严格置信准入另有实现与单元测试，不能给经验结果套用严格概率保证。','']
    return '\n'.join(text)


def main():
    p=argparse.ArgumentParser();p.add_argument('reports',nargs='+',type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(summarize(args.reports),encoding='utf-8')
    print(args.output.resolve())


if __name__=='__main__':main()
