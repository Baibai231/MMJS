"""Assemble the competition report from verified, immutable experiment records."""
from pathlib import Path
from collections import Counter
import json
import re
import html
import hashlib
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web.intervention_presentation import chart_specs
from tools.run_dynamic_study import svg_only
from policy.intervention_fragments import LABELS

DOCS = ROOT / 'docs'
RUN = ROOT / 'reports' / 'report_writing_v7_20261008'
ASSETS = DOCS / 'report_assets_20261008'
ASSETS.mkdir(exist_ok=True)
report = json.loads((RUN / 'report.json').read_text(encoding='utf-8'))
benchmark = json.loads((RUN / 'report_shape_benchmark.json').read_text(encoding='utf-8'))
historical_path = ROOT / 'reports' / 'intervention' / '975472d3703d4c61' / 'report.json'
historical = json.loads(historical_path.read_text(encoding='utf-8'))
assert report['metadata']['protocol'] == 'selective-intervention-v7-ideal-wasserstein'
assert report['config']['data']['users'] == 1000
arms = report['google_round_zipf']['arms']
names = {'baseline': 'G0 原始组', 'google_hold': 'G1 Google 保持',
         'google_random': 'G2 随机分批', 'google_frozen': 'G3 初始规划',
         'google_dynamic': 'G4 动态反馈'}


def table(headers, rows):
    def cell(x):
        return str(x).replace('|', '\\|').replace('\n', '；')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(cell(x) for x in row) + ' |' for row in rows])


def pct(x):
    return f'{100*x:.2f}%'


def point(e):
    return e['minauto'][-1]


def state(a):
    return a['final']


def current_tables():
    dist, attack, cost = [], [], []
    all_states = [('baseline', report['baseline'], None)] + [(k, state(a), a) for k, a in arms.items()]
    for key, s, a in all_states:
        dist.append([names[key], s['distribution']['unique'],
                     f"{s['ideal_distance']['empirical']:.9f}", f"{s['ideal_distance']['fitted']:.9f}"])
        f = point(s['risk'])
        a1 = point(a['attacks']['A1']) if a else None
        attack.append([names[key], f"{s['attack_log_area']:.9f}", pct(f['rate']),
                       pct(a1['rate']) if a1 else '未训练',
                       pct(f['outside_model_support_weight']/1000),
                       pct(a1['outside_model_support_weight']/1000) if a1 else '不适用'])
        cost.append([names[key], len(a['rounds']) if a else 0,
                     s['ledger']['adaptive_affected'], pct(s['ledger']['adaptive_affected_rate']),
                     sum(r.get('changed', 0) for r in a['rounds']) if a else 0,
                     a['stop_label'] if a else '保留原始状态'])
    return (table(['组别', '不同口令数', '经验距离 $D$', '拟合距离 $\\widehat D$'], dist),
            table(['组别', '$A^F$', '$S^F(10^6)$', '$S^{A1}(10^6)$', 'F 未覆盖', 'A1 未覆盖'], attack),
            table(['组别', '执行轮数', '后续通知人数', '后续通知比例', '后续提交修改数', '停止原因'], cost))


def audit_tables():
    rows, individual, params = [], [], []
    for key in ('google_dynamic', 'google_frozen'):
        for r in arms[key]['rounds']:
            guard = r['aggregate_attack_guard']
            rows.append([names[key], r['round'], r['action']['selected'], r['changed'],
                         f"{guard['before_area']:.9f}", f"{guard['after_area']:.9f}",
                         '接受' if guard['accepted'] else '拒绝',
                         '；'.join(guard['rejection_reasons']) or '两项条件均通过'])
            z = r['strength_diagnostics']
            individual.append([names[key], r['round'], r['changed'], z['improved'],
                               z['weakened'], z['equal'], z['uncomparable']])
    for r in report['round_parameter_fits']['rows']:
        s = arms['google_dynamic']['trajectory'][r['round']]
        params.append([r['round'], f"{r['parameters']['c']:.9f}", f"{r['parameters']['s']:.9f}",
                       r['unique_passwords'], f"{s['ideal_distance']['empirical']:.9f}",
                       f"{s['ideal_distance']['fitted']:.9f}", pct(s['ledger']['adaptive_affected_rate'])])
    return (table(['组别', '轮次', '通知', '提交修改', '验收前 $A^F$', '提交后 $A^F$', '决定', '原因'], rows),
            table(['组别', '轮次', '实际修改', '变强', '变弱', '相同', '无法比较'], individual),
            table(['轮次', '$c$', '$s$', '不同口令数', '经验距离', '拟合距离', '后续通知比例'], params))


distance_table, attack_table, cost_table = current_tables()
guard_table, individual_table, parameter_table = audit_tables()

# Reuse the project's vector plotting implementation, with the raw F reference
# removed from the A1 plot: the raw population has no independently trained A1.
specs = chart_specs(report)
selected_charts = ['final_distribution', 'distribution_cost', 'attack_F', 'attack_A1',
                   'attack_area_cost', 'google_round_zipf', 'round_parameters']
for key in selected_charts:
    if key == 'round_parameters':
        shutil.copyfile(RUN / 'round_parameters.svg', ASSETS / 'round_parameters.svg')
        ET.parse(ASSETS / 'round_parameters.svg')
        continue
    title, series, opts = specs[key]
    if key == 'attack_A1':
        series = series[1:]
    svg = svg_only(series, **opts)
    ET.fromstring(svg)
    (ASSETS / (key + '.svg')).write_text(svg, encoding='utf-8')

# Keep anonymous frequency and evaluation records alongside the report.
for source, dest in [(RUN / 'report.json', ASSETS / 'current_v7_report.json'),
                     (RUN / 'report_shape_benchmark.json', ASSETS / 'shape_benchmark.json')]:
    shutil.copyfile(source, dest)

shape_rows = []
for model in benchmark['previous_models']:
    hold = benchmark['heldout_shape_check'][model['id']]
    shape_rows.append([model['name'], f"{model['ks']:.6f}", '解析模型，无该项再采样',
                       f"{hold['mean_max_cdf_error']:.6f}", f"{model['runtime_seconds']:.6f}"])
new = benchmark['sampling_fit']
shape_rows.append(['CDF 采样拟合', f"{new['training_max_cdf_error']:.6f}",
                   f"{new['replication']['mean_max_cdf_error']:.6f}",
                   f"{benchmark['heldout_shape_check']['cdf_sampling']['mean_max_cdf_error']:.6f}",
                   f"{new['runtime_seconds']:.6f}"])
shape_table = table(['方法', '全样本拟合误差', '独立采样平均误差', '留出形状平均误差', '参数拟合耗时/s'], shape_rows)

hist_rows = []
for key, a in historical['google_round_zipf']['arms'].items():
    s = state(a)
    hist_rows.append([names[key], len(a['rounds']), pct(s['ledger']['adaptive_affected_rate']),
                      f"{s['ideal_distance']['empirical']:.9f}", f"{s['ideal_distance']['fitted']:.9f}",
                      pct(point(a['attacks']['F'])['rate']), pct(point(a['attacks']['A1'])['rate'])])
hist_table = table(['历史组别', '轮数', '后续通知比例', '补算经验距离', '补算拟合距离', 'F 百万预算命中', 'A1 百万预算命中'], hist_rows)

full_rules = dict(LABELS)
full_rules.update({19: '长度至少 6 字符', 20: '长度至少 8 字符'})
rule_table = table(['完整池编号', '修改方法', 'Google 起点后的角色'],
                   [[k, v, '默认局部候选' if k <= 18 else '已由最低 8 字符起点满足'] for k, v in full_rules.items()])

continuation = r'''
### 2.3 模型框图及设计参数

系统的数据与决策路径见表 2-1。目标群体的账户状态产生当前分布，分布与剩余预算共同决定候选；预测产生临时提案，F 验收决定提交状态；提交结果回到分布分析，并驱动独立参考群体迁移。A1 位于各组终态评估阶段，其输出不回流到本次搜索。下列参数表区分主研究设定与本次流程验证的实际配置。

#### 2.3.1 数据与通知预算

**表 2-5 数据、基础规则与执行预算**

| 参数 | 主研究设定 | 本次流程验证 | 含义 |
| --- | --- | --- | --- |
| 目标账户数 $N$ | 100,000 | 1,000 | 一次实验内保持固定 |
| 开发出现记录数 | 100,000 | 3,000 | 与目标出现位置不重叠 |
| 开发训练/调参/验证划分 | 60%/20%/20% | 1,800/600/600 | 当前主流程主要使用训练部分 |
| 基础长度要求 | 至少 8 字符 | 至少 8 字符 | 所有后续候选持续满足 |
| 单轮通知上限 | 2%，即 2,000 人 | 2%，即 20 人 | 上限允许未用满 |
| 后续累计通知上限 | 20%，即 20,000 人 | 20%，即 200 人 | 按不同账户统计 |
| 通知规模档位 | 0.1%、0.2%、0.5%、1%、2% | 1、2、5、10、20 人 | 同时截断至可行动人数与剩余容量 |
| 最小动作规模 | 0.1% | 1 人 | 用于减少过小候选 |
| Google 起点比较轮数上限 | 10 | 10 | 覆盖通用配置中的 40 轮字段 |
| 每账户后续通知次数 | 最多 1 次 | 最多 1 次 | 拒绝批次与未响应同样消耗资格 |

预算在运行前确定，初始 Google 迁移不占用后续 20% 上限。通知比例用于衡量管理与用户扰动的代理成本，尚不能直接换算为记忆负担、满意度或流失率。成功修改量与通知量分别统计，使预算使用与实际迁移效果能够同时解释。

#### 2.3.2 响应与拟合参数

**表 2-6 行为模拟及拟合设定**

| 参数 | 当前值 | 用途与来源 |
| --- | --- | --- |
| 后续不响应概率 | 15% | 项目模拟假设 |
| 响应者修补/重组/重选权重 | 60%/25%/15% | 条件于已经响应，非修改成功率 |
| 每账户最大尝试次数 | 5 | 有限响应，失败保留原口令 |
| 修补行为中的简单追加倾向 | 55% | 内部行为假设，可能形成新后缀热点 |
| Google 构造阶段不响应概率 | 0% | 定义合规共同起点，失败再显式补全 |
| 初始化回归排名点 | 最多 1,024 个 | 对数间隔取点，仅给出搜索初值 |
| 参数范围 | $0<cleq1$、$0<sleq1$ | 限制采样模型有效域 |
| 初值邻域 | 初值的约 1/3 至 3 倍，再截断 | 有限搜索范围 |
| 粗网格 | $5	imes5$ | 避免仅依赖局部搜索 |
| 默认搜索迭代 | 12 | 嵌套黄金分割的工程预设 |
| 距离再采样次数 | 3 | 使用独立于拟合的采样流，平均距离 |
| 拟合诊断再采样次数 | 5 | 用于累计误差复核，与距离再采样不同 |

用户响应概率没有从所引用论文中直接取得。文献[2–3]用于支持政策与用户选择共同建模的必要性，实际参数属于可配置假设。参数作用应通过行为敏感性分析检验，而不以一次目标结果优劣反向确定。

#### 2.3.3 搜索及攻击评估参数

**表 2-7 搜索资源与猜测评估设定**

| 参数 | 当前设定 | 说明 |
| --- | --- | --- |
| 热门名单长度 | 20 | 同时要求频次至少 2 |
| 重点分组上限 | 12 | 保留部分结构组机会 |
| 部分结构候选短名单 | 80 | 跨结构与随机候选额外保留，不是全部动作总数 |
| 粗评响应重复 | 3 | 比较经验距离预计变化 |
| 常规拟合复核短名单/响应重复 | 5/5 | 没有可行方案时可继续复核后续候选 |
| 组合搜索宽度 | 6 | 有限、不重叠的子动作组合 |
| 两步前瞻宽度/预演次数 | 2/2 | 仅在普通搜索失败后启用 |
| 第一阶段经验距离损失筛选阈值 | 0.0003 | 前瞻筛选参数，不是实际收益保证 |
| 分布正收益及 F 验收容差 | $10^{-12}$ | 数值比较阈值，不代表统计显著性 |
| 猜测预算点 | $1,10,ldots,10^6$ | 所有组共用七个点 |
| PCFG 蒙特卡洛样本 | 主设 100,000；本次 10,000 | 估计概率排序下的猜测次数[7] |
| PCFG 训练超时 | 主设 3,600 秒；本次 180 秒 | 工程资源上限 |
| 本次种子 | 42 | 单次实例，不构成多种子统计证据 |
| A1 | 各组终态独立参考训练 | 当前主流程不逐轮用 A1 选动作 |

PCFG 使用项目固定版本的开源实现[8]。本作品汇总同一字符串的多条语法推导概率，并以完整字符串的边际概率及固定同概率排序约定估计猜测次数。其排序对象与实现细节需随模型版本记录，不能简单等同于原始文法解析队列的枚举顺序。

部分配置字段来自历史研究流程。当前五组主比较中的目标改善比例、连续停滞次数、HHI 增长上限和 A1 回归上限等，不应仅因出现在配置文件中就写成生效约束。正文列出的实际接受条件以 2.2.4 节和执行账本为准。

### 2.4 软件流程与停止机制

#### 2.4.1 总体软件流程

系统先检查配置、语料格式与样本容量，再通过扫描统计有效频次并抽取不重叠出现位置。缓存命中时仍核对语料内容摘要，避免在输入发生变化后继续使用旧统计。随后训练 F、构造 Google 共同起点并核验四组账户状态一致，再分别运行三种局部方法。各组结束后训练对应 A1，完成终态拟合、逐轮参数诊断和报告导出。

该流程保留配置、数据摘要、源码摘要和运行标识，使不同协议的结果能够被识别。用户加载历史结果时，应根据结果自身的协议解释动作与曲线，不能把新界面的指标名称当作历史控制器已经采用该指标的证据。

#### 2.4.2 单轮执行与异常处理

单轮执行包括预算检查、候选生成、预测复核、独立响应执行、整批 F 验收、账户状态更新和账本保存。提交前检查账户标识去重、口令与当前状态一致及基础规则合规；拒绝批次保留通知成本并记录拒绝原因。候选预测失败与正式提案拒绝分别记录：前者没有产生通知，后者已经消耗本轮预算。

配置项超出范围、数据数量不足或 PCFG 训练失败时，任务输出错误状态及原因，不将未完成状态写为成功结果。训练超时是工程失败原因，与没有可行政策属于不同类型的停止。

#### 2.4.3 停止条件

**表 2-8 主要停止情形及其含义**

| 情形 | 触发条件 | 报告处理 |
| --- | --- | --- |
| 预算用尽 | 后续累计容量不足 | 保留末次实际执行及未使用容量说明 |
| 达到轮数上限 | 已执行 10 轮 | 按实际终态比较，不追加虚构轮次 |
| 没有可行候选 | 普通、组合和允许的前瞻均未找到可接受方案 | 保存候选排除审计 |
| 初始计划结束 | 冻结计划已执行完 | 保留现有状态，不重新规划 |
| 没有可行动对象 | 未通知账户中无相应整改对象 | 报告对象耗尽 |
| 配置、数据或训练异常 | 输入或模型构建无法完成 | 记录失败阶段，不混入效果统计 |

当前 Google 动态组并不因一次实际距离未下降就直接停止。下一轮仍可重新搜索；若没有可行候选，才形成停止审计。停止审计不构成额外执行轮次。有限搜索范围内未找到动作，只描述本次控制器和资源限制下的结果。

### 2.5 软件与系统功能

系统提供本地网页展示台与命令行入口。参数配置覆盖账户规模、开发样本、通知比例、不响应概率、随机种子和蒙特卡洛样本数；后台任务显示语料扫描、候选比较、模型训练及导出进度。展示层将五组分布、F/A1 曲线、逐轮动作和成本记录关联起来，并提供向量图和结构化结果导出。

逐轮审计展示分组、规则、通知人数、实际修改、预测与实现收益、F 验收结果及停止原因。历史结果保留协议标识；网页截图和图表用于说明功能及数据表达，效果结论以结构化实验记录为依据。当前验证覆盖离线计算、结果生成和图形导出，本报告不将其扩展为真实网站的线上部署测试。

### 2.6 评价指标及解释

**表 2-9 评价指标及其角色**

| 指标 | 方向与用途 | 解释范围 |
| --- | --- | --- |
| 经验距离 $D$ | 越小越接近理想单例参照 | 实际完整口令频次的分散程度 |
| 拟合距离 $widehat D$ | 候选主排序，越小越好 | 采样模型描述下的分布状态 |
| 参数 $c,s$ | 描述逐轮形态 | 不直接表示口令强度 |
| 最大累计拟合误差 $E$ | 越小表示模型更贴近观测 | 拟合质量，不是安全分数 |
| F 曲线积分 $A^F$ | 越小越好，整批验收条件 | 固定模型、既定预算网格下的总体估计 |
| $S^F(b)$、$S^{A1}(b)$ | 指定预算下越低越好 | 所用模型的估计命中率 |
| 未覆盖及低采样支持比例 | 与曲线同时报告 | 区分模型支持范围与估计信息不足 |
| 后续不同通知比例 $Q=\lvert U\rvert/N$ | 主要扰动成本 | 失败与拒绝通知均计入 |
| 后续成功修改数 | 描述实际迁移量 | 与 Google 初始修改分开 |
| 归一化编辑距离 | 描述字符串修改量 | 尚不代表真实记忆成本 |
| 个体强度分类 | 变强、变弱、相同、无法比较 | 以固定 F 的估计次数比较 |
| 不同口令数、HHI、头部占比 | 辅助检查热点变化 | 不替代主距离或攻击评估 |
| 运行时间与候选数 | 计算代价 | 与机器和缓存状态关联 |

后续距离相对改善采用共同 Google 起点作为参照：

$$
R_D=rac{D_{mathrm{Google}}-D_{mathrm{final}}}{D_{mathrm{Google}}},
qquad D_{mathrm{Google}}>0.
$$

拟合相对改善同理计算并单独报告。攻击命中率的百分点变化与相对百分比变化分别表达；不同预算下的曲线可能交叉，因此一个终点与整个对数预算积分回答不同问题。

## 第三章 作品测试与分析

### 3.1 测试方案与证据范围

本章检验三个层面：模型能否描述完整频次，控制器能否执行预算与验收约束，以及不同决策方式在已完成实验中产生何种分布与猜测结果。本次补充运行采用当前 v7 协议、1,000 个目标账户和单一种子，作为端到端流程验证；同时对同一原始频次补做三种拟合方法的比较。已有 10 万账户 v5 结果仅用于历史探索分析，其理想距离为事后重算。

**表 3-1 测试项目、已有材料与结论范围**

| 项目 | 本报告采用的材料 | 可支持的判断 |
| --- | --- | --- |
| 拟合比较 | 同一 1,000 账户频次、独立再采样及 70/30 形状留出 | 当前样本的拟合误差与计算代价 |
| 当前协议流程 | v7 单种子五组执行、逐轮账本、F/A1 终态 | 预算、提案验收和反馈流程是否按定义运行 |
| 约束核验 | 距离、拟合、整批验收共 19 项自动检查 | 被检查的计算与状态处理行为 |
| 历史大样本 | 10 万账户 v5 记录及事后距离计算 | 历史设定下的探索现象 |
| 新协议主效果与稳健性 | 尚无同协议 10 万账户配对重复 | 当前不足以支持稳定优势或统计显著性 |

两种规模实验均在自身固定 $N$ 的支持范围上使用 $N$ 个单例作为参照。1,000 账户的距离不与 10 万单例直接比较，跨规模数值也不作为控制器优劣的直接证据。

### 3.2 测试环境与威胁模型

当前验证在 Windows 11 环境运行，Python 版本为 3.13.9，解释器报告 16 个逻辑处理器。PCFG 上游固定提交为 `b04bbdadfe8928fd1287fa73ad1aa46a297ff83a`，拟合实现标识为 `cdf-zipf-sampling-gss-v1`。本次验证未完整记录 CPU 型号与物理内存，因此耗时作为当前机器的运行记录，不用于硬件标准化比较。

威胁模型为具有有限猜测预算的离线攻击者。F 使用独立开发训练记录建立并冻结；A1 知道公开的局部动作，并依据独立参考群体的模拟终态训练。A1 不采用目标终态作为训练集。PCFG 与蒙特卡洛估计提供概率排序下的可猜测性度量[4,7]，不覆盖所有攻击算法、定向个人信息攻击或现实攻击者的全部知识。模型族与训练来源的限制需结合覆盖率解释[5]。

### 3.3 测试数据与公平性设置

#### 3.3.1 数据来源与角色

本项目采用本地 `rockyou-withcount.txt` 带频次语料。当前扫描清单记录原始行数 14,344,391、无效行数 10、有效出现次数 32,603,039，编码为 Latin-1。频次用于模拟账户出现概率；语料出现次数没有被核实为独立真实账户。文件内容摘要见附录 C，用于识别本报告采用的具体数据版本。

本次验证抽取 1,000 个目标出现位置及 3,000 个开发位置。开发记录分为 1,800 个训练、600 个调参和 600 个验证位置。主干预流程使用训练部分建立 F、响应词池与独立参考群体。拟合比较中的 70/30 留出是对目标原始频次单独开展的形状诊断，与上述开发角色划分属于不同操作。

#### 3.3.2 五组及附加策略对照

**表 3-2 实验组别与控制条件**

| 组别 | 决策与执行 | 主要比较目的 |
| --- | --- | --- |
| G0 原始组 | 保留原始状态 | 给出原始分布和固定 F 参照 |
| G1 Google 保持 | 构造最低 8 字符共同起点，此后保持 | 分析基础迁移 |
| G2 随机分批 | 随机抽取可行动账户，再筛选规则分配 | 检查局部定向选择的价值 |
| G3 初始规划 | 在共同起点冻结计划，后续验收执行 | 比较分布反馈的作用 |
| G4 动态反馈 | 每轮根据新状态重新选动作 | 分析完整决策闭环 |

G1—G4 的账户级起点摘要一致，三个局部组共享规则池、响应设定、F 验收和通知上限。随机组仍进行收益筛选，因此其停止不等于随机账户没有任何可修改规则。不同组的实际通知规模和停止时点可能不同，终态比较须同时列出成本。

系统另运行 Yahoo! JAPAN 命名的全站策略对照，按项目规则记录实现 15—32 字符、有限允许字符及禁止组合。该对照从原始状态一次迁移，采用强制合规和显式补全，不受局部 20% 预算或 F 验收门槛约束。它用于说明大范围规则变更与局部通知的成本差异，不纳入五组同预算优劣判断。对应帮助页见文献[10]，页面依赖 JavaScript，政策快照的来源限制在附录 A 中说明。

#### 3.3.3 共同成本比较原则

横轴采用 Google 起点后的累计不同通知比例。曲线只画到各组实际到达的成本，提前停止后不外推到 20%。实际共同成本点优先用于对比；没有共同点时，终态差异仅作带成本的描述，不据此归因于动态选人或反馈。

本次 G3 与 G4 在后续通知 3% 时具有相同状态，G3 之后继续执行至 7%，G4 则停止于 3%。G2 没有执行局部批次，因而本次数据不足以形成三个局部组在 20% 预算处的完整比较。未用满预算与停止原因均作为结果呈现。

### 3.4 测试结果与分析

#### 3.4.1 拟合精度与计算代价

拟合比较使用原始组的 1,000 账户频次，共 982 个不同完整口令。比较离散 Zipf、有限支持 CDF-Zipf 与 CDF 采样拟合；误差统一为完整支持上的最大累计差。采样方法另用五个独立采样种子复核。留出检查将出现次数按随机二项划分得到 715 个训练账户和 285 个验证账户，验证频次独立重排序，各模型生成验证规模样本后再比较形状。

**表 3-3 原始频次的拟合比较**

{{SHAPE_TABLE}}

在该样本中，采样模型的全样本拟合误差为 0.004000，独立采样平均误差为 0.006200；其留出形状误差低于两种有限支持解析模型。与此同时，参数拟合耗时明显高于解析模型。由于模型支持假设、参数空间和采样后重排序过程均不同，误差差异不能全部归因于参数搜索算法，也不能由此推出猜测风险降低。一次留出划分与五个模拟种子仍不足以构成跨数据源验证。

#### 3.4.2 当前协议的分布与成本

共同 Google 起点通知并修改 519 个短口令账户，其中 4 个通过显式长度补全完成合规；其余 481 个账户原本已满足基础长度要求。该阶段后的经验距离由 0.016124466 降至 0.007459600，不同口令数由 982 增至 991。基础迁移采用全体合规构造，因此这些人数反映本次模拟操作。

**表 3-4 当前 v7 五组的终态分布**

{{DISTANCE_TABLE}}

**表 3-5 后续执行成本与停止情况**

{{COST_TABLE}}

G4 在后续通知 30 人后形成 7 次提交修改，经验距离降至 0.006761793，相对共同起点缩小 9.35%；拟合距离由 0.007237371 降至 0.005654052，相对缩小 21.88%。二者改善幅度不同，表明拟合状态与实际频次之间仍存在描述差异，应同时解释。

G3 使用更多通知预算后达到更低的终态经验距离，但其与 G4 在 3% 通知处的状态相同。本次数据因此没有证明动态反馈优于初始规划。G2 因未找到满足条件的动作而停止于起点，不能将其零修改结果扩展为随机干预在其他样本中无效。

![当前协议终态完整口令频次](report_assets_20261008/final_distribution.svg)

**图 3-1 当前协议完整排名频次。** 目标账户数为 1,000，理想虚线为 1,000 个不同口令各 1 人。图中附加 Yahoo 对照采用独立全站迁移条件。

![分布距离与后续通知成本](report_assets_20261008/distribution_cost.svg)

**图 3-2 拟合距离与后续通知成本。** 每条曲线仅到实际执行终点；G0、G1 及未执行的 G2 位于零后续成本。该图不包含把未用预算补齐的虚构状态。

#### 3.4.3 固定与适应性猜测结果

**表 3-6 当前协议的猜测评估及模型覆盖**

{{ATTACK_TABLE}}

G4 的 F 总体积分由 0.038500000 降至 0.038166667，但在百万预算处的估计命中率仍为 9.40%；A1 终点仍为 10.60%。因此，本次结果支持指定预算网格上的固定 F 汇总改善，不支持 A1 终点风险下降。

G3 的 F 总体积分更低，但其 F 百万预算终点由 9.40% 升至 9.60%，A1 终点为 10.80%。这说明曲线积分下降与每个预算点下降并不等价。原始组的 F 终点为 8.20%，Google 起点为 9.40%，基础迁移也未在该终点产生下降。

本次小开发训练集导致 F 与 A1 未覆盖比例较高。G4 的 F 未覆盖比例为 89.20%，A1 为 81.50%。曲线以全部账户为分母，模型未覆盖质量不计入已命中；这些口令的实际攻击难度仍未确定。高未覆盖比例显著限制了安全结论，需要扩大训练支持并增加独立模型验证[5]。

![固定 F 猜测曲线](report_assets_20261008/attack_F.svg)

**图 3-3 固定 F 的估计累计成功率。** 七个猜测预算点，全部账户分母；含附加全站对照。

![终态 A1 猜测曲线](report_assets_20261008/attack_A1.svg)

**图 3-4 各组终态 A1 的估计累计成功率。** 每个模型训练于对应独立参考终态。原始组未单独训练 A1，因此不将其 F 曲线冒充 A1 放入本图。

#### 3.4.4 动态轨迹与总体约束核验

G4 第一轮选择“至少一个英文字母”的跨结构账户，通知 20 人；本次响应提案使 F 总体积分增加，因此整批拒绝。第二轮选择“至少一个数字或符号”，通知 10 人并提交 7 次修改；F 总体积分下降且未覆盖比例不增加。下一轮搜索未找到可行候选，停止于两轮实际执行。

**表 3-7 两个局部组的批次验收记录**

{{GUARD_TABLE}}

G3 第一轮与 G4 相同，第二轮也产生相同提交状态；第三轮提交 14 次修改，第四轮虽然提案 F 积分下降，却因未覆盖比例增加而拒绝。预算与验收的两项条件均在实际执行中产生了可辨认的作用。

**表 3-8 动态组的频次与拟合轨迹**

{{PARAMETER_TABLE}}

![动态组逐轮频次](report_assets_20261008/google_round_zipf.svg)

**图 3-5 共同起点及 G4 实际执行后的频次。** 第一轮拒绝后的曲线与起点重合，停止审计不作为第三次修改。

![动态组参数轨迹](report_assets_20261008/round_parameters.svg)

**图 3-6 CDF 采样参数的逐轮变化。** 参数用于形态诊断，实际效果由距离和攻击评估共同解释。

个体分类根据修改前后固定 F 的可用估计猜测次数计算，见表 3-9。

**表 3-9 实际修改账户的固定 F 诊断**

{{INDIVIDUAL_TABLE}}

G4 第二轮 7 名修改账户均无法进行完整的前后估计比较。G3 第三轮则同时出现变强与变弱账户。总体未覆盖比例不增加允许未知与可用状态之间的迁移相互抵消，因此它既不保证每名账户都可比较，也不提供逐账户安全保证。

#### 3.4.5 历史 10 万账户探索

历史记录 `975472d3703d4c61` 的决策协议为 v5，其父记录为 `ef77ab5e267c09c5`。该阶段采用拟合累计面积目标与旧的个体响应保障；后续扩展保持原动作和攻击结果，仅补算固定理想参照距离及附加全站对照。表 3-10 因而属于历史状态的重分析，不能替代 v7 主实验。

**表 3-10 历史协议终态的事后距离分析**

{{HISTORY_TABLE}}

在该历史实例中，动态组用 12.30% 的后续通知比例获得约 0.025562474 的经验距离，初始规划组用 14.80% 获得约 0.025711713。两组终态接近，实际成本不同。该现象说明较细粒度的局部动作具有进一步研究价值，但单一种子、不同终态成本和旧接受机制限制了结论。本报告不将这些数值与当前小规模验证合并计算均值或显著性。

#### 3.4.6 软件检查与计算开销

本次针对 CDF 采样与频次还原的 6 项、理想距离与附加对照的 6 项、整批 F 验收的 7 项自动检查均通过，共 19 项。检查覆盖可复现采样、完整支持误差、平台频次还原、独立积分等价、理想零点、历史动作保持、全站对照与局部预算的区别、整批拒绝记账及允许个体变弱时的总体接受等行为。

端到端验证生成结构化结果、校验摘要、HTML 报告及向量图，内部记录总耗时为 {{RUNTIME}} 秒。该时间包含缓存复用下的语料核验、抽样、模型训练、候选搜索、拟合与附加对照，不代表冷启动或 10 万账户性能。单独拟合比较的耗时见表 3-3。当前记录没有可靠的逐阶段资源峰值，报告据此只给出总运行记录。

### 3.5 结果讨论与有效性边界

现有证据支持系统能够在固定群体中执行预算核算、规则约束、整批验收、状态反馈与终态评估。G4 的经验距离下降与提交记录一致，拒绝批次保留成本，提前停止也得到保留。算法正确运行与算法效果领先是不同层面的判断：本次共同成本下 G3、G4 状态相同，A1 终点未改善，尚不足以支持动态方法稳定优于其他方案。

分布理想只要求当前样本不重复，未指定口令内容。归一化对数坐标改变了对头部集中和尾部质量的权重；跨规模比较、其他坐标和其他距离可能产生不同解释。因此，应先冻结距离与支持定义，再开展主实验，不把多个指标中事后最有利的一项当作唯一证据。

当前行为模型、同源语料和 PCFG 支持范围共同影响结果。不响应概率、常见修补倾向与重选池可能诱导新热点，单一模型族也可能偏好某些结构。有限候选及两步前瞻的停止只说明本次搜索未发现可行动作。真实部署还需要受保护的分布统计、真实响应数据和可执行的批次提交流程。

后续确认性实验应在 v7 及冻结参数下完成 10 万账户五组比较，并预先指定配对种子、行为情景和共同成本节点。建议使用种子 42—46，以 0%、15%、30% 不响应和少量预先定义的修改偏好开展敏感性分析。每个场景报告全部配对差值、未达到的成本点、拒绝与停止记录，再据此界定有效条件；本报告未将该计划写成已完成的稳健性结论。

## 第四章 创新性说明

### 4.1 通知预算下的局部动作建模

已有研究将口令分布和用户选择纳入政策生成与优化[1–2]。本作品将决策对象进一步限定为存量账户，在明确通知预算下联合组织对象、规则和规模，而非仅在整套网站政策之间切换。完整方法池来源于已有网站政策，贡献在于它与局部执行、未响应状态及预算账本的结合。该建模机制已实现，成本效益优势仍需通过相同成本的主实验确认。

### 4.2 完整频次反馈驱动的多轮决策

动态与适应性政策已有研究基础[1,3]。本作品的具体设计是每轮依据完整字符串频次更新对象与规则适用性，再用有限响应模拟比较局部动作，并在普通选择失败时使用有界前瞻。此处的特色是将反馈落实到预算内账户迁移的闭环过程。当前验证显示控制器能够更新并停止，但与初始规划的共同成本状态相同，反馈的额外效果尚未得到稳定实验证明。

### 4.3 理想距离与猜测接受条件的分离

Wasserstein-1 是已有概率分布距离[6]，PCFG 与蒙特卡洛可猜测性评估也来自已有方法[4,7]。本作品明确选择固定单例参照和对数排名几何，使频次效果具有一致基准；同时采用固定 F 的整批接受条件及终态 A1，避免由距离变化直接给出安全结论。

这一组合提供可分别审查的分布、风险与成本证据。它不改变 Wasserstein 公式本身，也不赋予模型外口令绝对安全性。固定 $N$ 下与旧累计目标的常数差关系已在 2.2.4 节说明，不能据此宣称仅切换为距离就提高了优化能力。

### 4.4 可追溯的政策实验实现

本作品将共同起点核验、候选预测、提交结果、成本、拒绝原因与停止记录保存为同一实验的结构化材料。历史协议与新协议可分别识别，经验指标与拟合指标也分别报告。这一实现有助于复核政策效果和失败原因。软件功能与实验设计属于系统贡献，效果数值及部署能力仍以已完成测试为界。

**表 4-1 贡献、实现与现有证据**

| 贡献 | 实现位置 | 当前证据 | 尚需确认 |
| --- | --- | --- | --- |
| 局部对象—规则—规模决策 | 候选与控制器 | 20/18 方法池、预算及实际动作账本 | 同成本稳定收益 |
| 逐轮分布反馈 | 状态统计、候选重建与有限前瞻 | 当前轨迹与停止审计 | 反馈相对初始规划的增量价值 |
| 分布与猜测分别核验 | W1、F 验收、终态 A1 | 距离变化、拒绝批次与风险曲线 | 更大模型支持及独立攻击族 |
| 复现与审计 | 配置、摘要、导出 | 实验标识、自动检查及匿名结果 | 新协议大样本配对重复 |

## 第五章 总结与后续工作

### 5.1 工作总结

本作品针对存量账户口令整改中的有限通知资源，构建了局部动态分布优化与评估系统。系统以完整口令频次为对象，将 20 条来源方法整理为 Google 起点后的默认 18 条候选，结合有限用户响应模拟选择对象、规则和通知规模。频次效果以与固定单例参照的 Wasserstein-1 距离度量，实际提交受固定 F 总体条件约束，并在终态利用独立参考群体训练 A1。

本次 v7 小规模验证完成五组运行、预算核算与结果导出，19 项相关自动检查通过。动态组使用 3% 后续通知，经验距离较 Google 起点缩小 9.35%，但 A1 百万预算终点未下降；初始规划在相同通知点具有相同状态。现有结果说明系统能够执行设计流程，尚不能据此主张动态方案全面领先。历史 10 万账户分析为进一步实验提供线索，其协议差异单独保留。

### 5.2 当前局限

第一，用户行为由预设模型生成，通知比例和编辑量只是扰动代理，真实记忆与接受成本仍未知。第二，目标与开发样本来自同一语料母体，原始出现记录缺乏独立账户身份核验。第三，F 与 A1 同属 PCFG 模型族，本次小训练集覆盖不足，模型估计不能代表所有攻击者。

第四，拟合采样、短名单和有限前瞻带来估计及搜索限制，未发现动作不等于政策空间已被穷尽。第五，批次整体接受允许局部个体变弱，真实逐用户即时修改流程未必满足暂存与统一提交假设。第六，当前 v7 缺少 10 万账户配对主实验和系统敏感性结果，因此统计优势与跨条件有效性仍待确认。

### 5.3 后续工作

首先，在冻结协议和参数后完成 v7 大样本配对实验，围绕共同成本和实际支持范围评价三种局部方案，避免以调参替代验证。其次，收集真实响应、尝试与修改成本资料，对行为设定开展预先定义的敏感性分析。再次，扩大攻击训练支持，在独立模型族和独立语料上复核，检查是否出现只对固定 F 有利的规则。

面向部署，需要进一步研究受保护的频次统计、用户交互与批次审批，确认建议怎样在真实口令保护边界内实施。规则附录还应保存 Top20 网站的原始条款快照与映射，拟合代码的作者、版本及获取信息也应补全，以保证方法归属和政策来源可核验。

{{REFERENCES}}

## 附录 A 完整修改方法池与来源说明

### A.1 完整 20 条方法

下表以默认池 1—18 的编号为基础，另将最低 6 字符、最低 8 字符列为 19、20，用于完整展示本报告的方法池。该编号是报告与实现的对照约定，不是网站排名，也不表示 20 条必须同时启用。

**表 A-1 完整 20 条修改方法及使用角色**

{{RULE_TABLE}}

字符范围、键盘名单、顺序序列及有限词表的实际判定见表 2-3。默认 18 条中部分要求具有交叉覆盖，保留独立条目是为了形成可比较的执行动作，不能由规则数量推导等量独立安全增益。

### A.2 网站条款与实现来源

网站资料是提取方法的直接来源，研究论文支持方法背景，二者承担不同引用职责。当前可核验的项目资料包含内部规则去重说明、规则判定实现和两项命名网站对照。Top20 网站到每条方法的完整原始页面、条款及采集日期尚未形成可核验的一一映射，本报告不补造这份来源清单。正式提交前需归档原资料并完成规则映射。

Google 命名基线采用项目固定的最低 8 字符子集；本次查阅的 Google 帮助页[9]还涉及弱口令、历史复用、首尾空格等限制，并建议更长口令。该页面并不为本实验“仅长度 8”的整体政策提供完整复现依据。Yahoo 命名对照按项目保存的条款摘要实现，来源链接为文献[10]，本次重新访问只能取得依赖 JavaScript 的页面外壳，未独立重读全部条款。表述和结论因此限于具体实现的规则子集。

PCFG 实现来源见文献[8]及固定提交；CDF 采样拟合改写自项目提供代码。现有材料不足以确定原代码作者和外部发表信息，因此正文保留代码来源说明，并将搜索与复核改动作为本项目适配，不虚构论文归属或原创身份。

## 附录 B 参数、记账与复现说明

### B.1 有效参数与历史字段

正式运行应保存合并默认值后的配置，包括表 2-5—2-7 中的预算、响应、搜索和攻击参数。对于不同流程中未生效的字段，应以调用路径和审计结果为准。当前 Google 五组比较上限为 10 轮，通用配置的 40 轮、目标比例和停滞字段不替代该流程的实际停止规则。

### B.2 成本账本

账本分别保存 Google 通知、后续通知、累计不同通知人数、累计不同修改人数、修改事件数与归一化编辑量。基础阶段与后续阶段可能通知同一账户，因此两阶段人数相加不等于总不同账户数。当前验证中 G4 总不同通知人数为 539，而 519 次基础通知加 30 次后续通知为 549 次事件，差异来自两阶段账户重叠。

后续阶段每账户最多通知一次，所以后续不同人数与后续通知事件数相同。G4 后续提交 7 次修改，总修改事件数为 526，但不同修改账户数为 525；一个账户可能在基础迁移和后续迁移中分别修改。效果分析应使用对应字段而不混用事件与人数。

### B.3 复现顺序

1. 核对语料文件摘要和编码，读取固定协议的完整配置。
2. 按记录种子抽取不重叠出现位置，保存样本与开发角色摘要。
3. 用开发训练角色建立固定 F 和响应词池，构造并核对 Google 共同起点。
4. 分别执行随机、初始规划和动态组，保存每轮提案、提交、成本与停止审计。
5. 在对应独立参考终态训练 A1，计算终态距离、覆盖与猜测曲线。
6. 对原始匿名频次开展独立的拟合比较，导出记录、校验摘要及图表。

本报告附带当前匿名实验 JSON 与拟合比较 JSON，不附真实口令清单。可复现性依赖原语料、固定上游模型、源码版本和运行配置共同一致，单独随机种子不足以识别一次实验。

## 附录 C 实验记录与证据索引

**表 C-1 本报告主要证据来源**

| 证据 | 协议与标识 | 用途 |
| --- | --- | --- |
| 当前 1,000 账户记录 | v7，`1c08a5567e27a0c8` | 第三章当前分布、风险、轨迹与成本 |
| 当前拟合比较 | 相同原始频次，种子 42 | 表 3-3，715/285 留出形状诊断 |
| 历史 10 万账户父记录 | v5，`ef77ab5e267c09c5` | 识别历史执行协议 |
| 历史理想距离扩展 | `975472d3703d4c61` | 表 3-10，事后重分析 |
| 自动检查 | CDF 6 项、距离/附加对照 6 项、F 验收 7 项 | 被检查行为的正确性 |

本次语料 SHA-256：

`e9d22d5316e7668aba2e9fa1c22dfc6e5afd217a60defc8f3c7f68a4f48f84b9`

当前结构化记录与拟合比较分别保存在随附的 `current_v7_report.json` 和 `shape_benchmark.json`，其中保留数据角色、源码摘要、参数与图表来源。历史记录仍位于项目实验归档，未改写为当前协议结果。
'''

refs = '''## 参考文献

[1] XIAO Y, ZENG J. Dynamically Generate Password Policy via Zipf Distribution[J]. IEEE Transactions on Information Forensics and Security, 2022, 17: 835–848. DOI: [10.1109/TIFS.2022.3152357](https://doi.org/10.1109/TIFS.2022.3152357).

[2] BLOCKI J, KOMANDURI S, PROCACCIA A D, et al. Optimizing Password Composition Policies[C]//Proceedings of the 14th ACM Conference on Electronic Commerce. 2013: 105–122. DOI: [10.1145/2492002.2482552](https://doi.org/10.1145/2492002.2482552). [作者公开版本](https://arxiv.org/abs/1302.5101).

[3] SEGRETI S M, et al. Diversify to Survive: Making Passwords Stronger with Adaptive Policies[C]//Thirteenth Symposium on Usable Privacy and Security. 2017: 1–12. [会议出版页](https://www.usenix.org/conference/soups2017/technical-sessions/presentation/segreti).

[4] WEIR M, AGGARWAL S, DE MEDEIROS B, et al. Password Cracking Using Probabilistic Context-Free Grammars[C]//IEEE Symposium on Security and Privacy. 2009: 391–405. DOI: [10.1109/SP.2009.8](https://doi.org/10.1109/SP.2009.8).

[5] UR B, et al. Measuring Real-World Accuracies and Biases in Modeling Password Guessability[C]//24th USENIX Security Symposium. 2015: 463–481. [会议出版页](https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/ur).

[6] PANARETOS V M, ZEMEL Y. Statistical Aspects of Wasserstein Distances[J]. Annual Review of Statistics and Its Application, 2019, 6: 405–431. DOI: [10.1146/annurev-statistics-030718-104938](https://doi.org/10.1146/annurev-statistics-030718-104938). [作者公开版本，第 1.2 节](https://arxiv.org/html/1806.05500v3#S1.SS2)。

[7] DELL’AMICO M, FILIPPONE M. Monte Carlo Strength Evaluation: Fast and Reliable Password Checking[C]//Proceedings of the 22nd ACM SIGSAC Conference on Computer and Communications Security. 2015: 158–169. DOI: [10.1145/2810103.2813631](https://doi.org/10.1145/2810103.2813631). [作者公开全文](https://www.dcs.gla.ac.uk/~maurizio/Publications/ccs15.pdf).

[8] LAKIW. PCFG Password Cracker[CP/OL]. [2026-10-08]. [项目仓库](https://github.com/lakiw/pcfg_cracker). 本作品固定提交：`b04bbdadfe8928fd1287fa73ad1aa46a297ff83a`。

[9] GOOGLE. Create a strong password & a more secure account[EB/OL]. [2026-10-08]. [Google Account Help](https://support.google.com/accounts/answer/32040). 引用用途：说明公开规则及安全建议的范围；不据此声称完整复现 Google 政策。

[10] YAHOO! JAPAN. Yahoo! JAPAN IDに関するヘルプ：H000004639[EB/OL]. [2026-10-08]. [帮助页面](https://support.yahoo-net.jp/SccLogin/s/article/H000004639). 引用用途：标识项目规则摘要的来源链接；本次访问未取得完整 JavaScript 页面内容。
'''

tokens = {'SHAPE_TABLE': shape_table, 'DISTANCE_TABLE': distance_table,
          'COST_TABLE': cost_table, 'ATTACK_TABLE': attack_table,
          'GUARD_TABLE': guard_table, 'PARAMETER_TABLE': parameter_table,
          'INDIVIDUAL_TABLE': individual_table, 'HISTORY_TABLE': hist_table,
          'RULE_TABLE': rule_table, 'RUNTIME': f"{report['metadata']['runtime_seconds']:.2f}",
          'REFERENCES': refs.strip()}
for key, value in tokens.items():
    continuation = continuation.replace('{{' + key + '}}', value)
assert '{{' not in continuation

base = (DOCS / 'ZipfGuard_竞赛作品报告_阶段定稿.md').read_text(encoding='utf-8')
base = base.split('## 参考文献', 1)[0].rstrip()
abstract = '''## 摘要

存量账户口令整改既需要改善群体选择的集中现象，也需要控制通知与修改成本。针对这一问题，本作品构建 ZipfGuard 局部动态口令分布优化系统，以完整口令频次为统计对象，将干预表示为账户集合、修改规则与通知规模的组合，利用用户响应模拟比较候选，并在每轮执行后更新分布与剩余预算。

修改方法从 Top20 网站政策资料中整理，共 20 条。其中最低 6 字符和最低 8 字符已被实验的 Google 基础长度起点满足，后续默认使用其余 18 条。系统结合 CDF 采样拟合描述频次形态，以固定账户规模下每个账户使用不同完整口令为理想参照，采用归一化对数排名轴上的一维 Wasserstein-1 距离评价分散程度。整批提交另要求固定猜测模型 F 的总体曲线积分下降及未覆盖比例不增加，并利用独立参考群体迁移后训练的 A1 检查终态可猜测性。

在当前协议的 1,000 账户流程验证中，动态组使用 3% 后续通知预算，经验距离较 Google 起点缩小 9.35%，拟合距离缩小 21.88%；A1 在百万次估计猜测预算下的命中率保持 10.60%。初始规划组在相同通知点具有相同状态，19 项相关自动检查通过。结果说明系统能够执行预算、验收与反馈流程，但当前证据尚未建立动态方法的稳定优势。作品同时整理历史 10 万账户探索，明确其与当前协议的差异，为进一步配对主实验提供依据。

**关键词：** 口令分布；局部动态干预；Wasserstein-1 距离；通知预算；口令猜测评估

'''
start = base.index('## 摘要')
end = base.index('## 第一章')
base = base[:start] + abstract + base[end:]
base = base.replace('具体实现为最低 8 字符这一规则子集。',
                    '具体实现为项目固定的最低 8 字符规则子集，公开政策的更完整范围参见文献[9]。')
base = base.replace('PCFG 方法，其利用口令结构及组成片段概率开展猜测的基础来自 Weir 等的研究[4]。',
                    'PCFG 方法，其利用口令结构及组成片段概率开展猜测的基础来自 Weir 等的研究[4]，工程实现采用固定版本开源项目[8]。')
base = base.replace('**固定 F 的猜测评估：** 设',
                    '**固定 F 的猜测评估：** 蒙特卡洛采样估计概率模型下猜测次数的方法依据见文献[7]。本作品记录自身排序约定与采样支持范围。设')
base = base.replace('理想分布在对数坐标上的累计曲线无需为直线。',
                    '理想频次虚线对应的累计分布在对数坐标上并非直线。')
toc = '''## 目录

- 第一章 作品概述
- 第二章 作品设计与实现
- 第三章 作品测试与分析
- 第四章 创新性说明
- 第五章 总结与后续工作
- 参考文献
- 附录 A 完整修改方法池与来源说明
- 附录 B 参数、记账与复现说明
- 附录 C 实验记录与证据索引

'''
base = base.replace('## 第一章', toc + '## 第一章', 1)
full = base.rstrip() + '\n\n' + continuation.strip() + '\n'


def validate_markdown(text):
    assert not re.search(r'\\[\[\]()]', text), 'nonportable math delimiter'
    in_math = in_fence = False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith('```'):
            in_fence = not in_fence
        if line.strip() == '$$':
            in_math = not in_math
            continue
        if not in_math and not in_fence:
            assert len(re.findall(r'(?<!\\)\$', line)) % 2 == 0, f'math {i+1}'
        if line.startswith('#') and i:
            assert not lines[i-1].strip(), f'heading {i+1}'
        if line.startswith('|') and i and lines[i-1].startswith('|'):
            assert line.count('|') == lines[i-1].count('|'), f'table {i+1}'
    assert not in_math and not in_fence
    refs_part = text.split('## 参考文献', 1)[1].split('## 附录', 1)[0]
    defined = set(re.findall(r'^\[(\d+)\]', refs_part, re.M))
    cited = set()
    body = text.split('## 参考文献', 1)[0] + text.split('## 附录', 1)[1]
    for group in re.findall(r'\[(\d+(?:[–,]\d+)*)\]', body):
        for item in group.split(','):
            if '–' in item:
                a, b = map(int, item.split('–'))
                cited.update(map(str, range(a, b+1)))
            else:
                cited.add(item)
    assert defined == cited, (defined, cited)
    for path in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text):
        assert (DOCS/path).is_file(), path


validate_markdown(full)
out = DOCS / 'ZipfGuard_竞赛作品报告_完整版.md'
out.write_text(full, encoding='utf-8')

# An accessible reading copy: vectors embedded, MathJax enhances the readable
# TeX equations when internet is available; the Markdown source remains primary.
def inline(s):
    safe = html.escape(s)
    safe = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe)
    safe = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', safe)
    safe = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'<a href="\2">\1</a>', safe)
    return safe


def render(text):
    lines = text.splitlines()
    blocks, i, heading_id = [], 0, 0
    while i < len(lines):
        s = lines[i]
        if not s.strip():
            i += 1
            continue
        if s.startswith('#'):
            level = len(s)-len(s.lstrip('#'))
            heading_id += 1
            blocks.append(f'<h{level} id="section-{heading_id}">{inline(s[level:].strip())}</h{level}>')
            i += 1
        elif s.strip() == '$$':
            j = i+1
            while lines[j].strip() != '$$': j += 1
            blocks.append('<div class="equation">$$\n' + html.escape('\n'.join(lines[i+1:j])) + '\n$$</div>')
            i = j+1
        elif s.startswith('|'):
            j = i
            while j < len(lines) and lines[j].startswith('|'): j += 1
            rows = [[x.strip() for x in line.strip().strip('|').split('|')] for line in lines[i:j]]
            markup = '<div class="table-wrap"><table><thead><tr>' + ''.join('<th>'+inline(x)+'</th>' for x in rows[0]) + '</tr></thead><tbody>'
            for row in rows[2:]:
                markup += '<tr>'+''.join('<td>'+inline(x)+'</td>' for x in row)+'</tr>'
            blocks.append(markup+'</tbody></table></div>')
            i = j
        elif s.startswith('!['):
            m = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', s)
            svg = (DOCS/m[2]).read_text(encoding='utf-8')
            blocks.append('<figure aria-label="'+html.escape(m[1])+'">'+svg+'</figure>')
            i += 1
        elif re.match(r'^(?:- |\d+\. )', s):
            ordered = bool(re.match(r'^\d+\. ', s))
            tag = 'ol' if ordered else 'ul'
            items = []
            while i < len(lines) and re.match(r'^(?:- |\d+\. )', lines[i]):
                items.append('<li>'+inline(re.sub(r'^(?:- |\d+\. )', '', lines[i]))+'</li>')
                i += 1
            blocks.append('<'+tag+'>'+''.join(items)+'</'+tag+'>')
        else:
            parts = [s]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r'^(?:#|\||!\[|\$\$|- |\d+\. )', lines[i]):
                parts.append(lines[i]); i += 1
            blocks.append('<p>'+inline(' '.join(parts))+'</p>')
    return '\n'.join(blocks)


css = '''body{max-width:1100px;margin:32px auto;padding:0 24px 80px;color:#172b36;background:#fbfcfd;font-family:"Microsoft YaHei","Noto Sans SC",sans-serif;line-height:1.85;font-size:16px}h1{font-size:30px}h2{margin-top:52px;border-bottom:2px solid #d8e4ea;padding-bottom:8px}h3{margin-top:30px}h4{margin-top:22px}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px;margin:20px 0}td,th{padding:10px;border:1px solid #dce5eb;text-align:left;vertical-align:top}th{background:#edf4f7}figure{margin:26px 0;background:#fff;padding:12px;overflow:auto}svg{max-width:100%;height:auto}a{color:#155e86}code{font-size:13px;overflow-wrap:anywhere}.equation{overflow:auto;padding:12px;text-align:center}p{margin:16px 0}@media print{body{background:white;font-size:11pt;max-width:none;margin:0}h2{break-before:page}table,figure{break-inside:avoid}a{color:inherit;text-decoration:none}}'''
html_document = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ZipfGuard 竞赛作品报告</title><style>'+css+'</style><script>window.MathJax={tex:{inlineMath:[["$","$"]],displayMath:[["$$","$$"]]},options:{skipHtmlTags:["script","style","textarea","pre","code"]}};</script><script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script></head><body>'+render(full)+'</body></html>'
(DOCS / 'ZipfGuard_竞赛作品报告_完整版.html').write_text(html_document, encoding='utf-8')
checks = {'current_run': report['metadata']['run_id'], 'current_protocol': report['metadata']['protocol'],
          'current_report_sha256': hashlib.sha256((RUN/'report.json').read_bytes()).hexdigest(),
          'historical_report_sha256': hashlib.sha256(historical_path.read_bytes()).hexdigest(),
          'report_sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
          'checks_passed': 19, 'current_users': 1000, 'current_development': 3000,
          'current_runtime_seconds': report['metadata']['runtime_seconds'],
          'native_v7_full_100k_available': False,
          'source_mapping_complete': False, 'cdf_original_authorship_complete': False,
          'markdown_structure_valid': True, 'citations_resolve': True,
          'notes': ['HTML embeds all vector figures; MathJax equation enhancement requires internet.',
                    'Historical v5 endpoints are never relabeled as v7 outcomes.',
                    'No target password strings are published in the supplied evidence records.']}
(ASSETS/'evidence_manifest.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
record = '''# ZipfGuard 完整报告编写记录

日期：2026 年 10 月 8 日。

## 已交付

- 全文完成摘要、五章正文、参考文献及三个附录，沿用用户已确认的 20/18 方法池和 W1 口径。
- 新运行 v7 的 1,000 账户验证，记录为 1c08a5567e27a0c8；未用历史 10 万结果冒充新协议主实验。
- 对相同原始频次补做三方法及 715/285 留出拟合比较。
- CDF 6 项、距离及附加对照 6 项、总体 F 验收 7 项检查通过。距离组首次导出检查受系统临时目录权限影响，改用工作区临时目录后通过，未更改测试或算法。
- 正文提供 23 张表、7 张向量图、10 项参考文献及分布/攻击/通知成本解释。
- HTML 已内嵌图表，公式增强采用 MathJax，联网可显示排版公式；Markdown 保留完整公式源码。

## 提交前仍需补充的证据

1. 新协议 10 万账户配对主实验、行为及预算敏感性结果；当前报告如实将其写为后续工作。
2. Top20 网站原始条款快照、各方法的规则来源映射及采集日期。
3. CDF 原始拟合代码作者或提供者、版本与获取信息。
4. Yahoo 条款页面依赖 JavaScript，本次未获取完整正文；报告只描述项目现有规则子集并标明来源状态。

全文已完成不等于以上未完成实验已经得到结果。本文件为内部记录，不并入正文。
'''
(DOCS/'ZipfGuard_阶段定稿_编写记录.md').write_text(record,encoding='utf-8')
bundle = DOCS / 'ZipfGuard_竞赛作品报告_完整交付包.zip'
with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for p in [out,DOCS/'ZipfGuard_竞赛作品报告_完整版.html']:
        z.write(p,p.name)
    for p in sorted(ASSETS.iterdir()):
        if p.is_file():z.write(p,'report_assets_20261008/'+p.name)
print(json.dumps({'markdown':str(out),'html':str(DOCS/'ZipfGuard_竞赛作品报告_完整版.html'),
                  'bundle':str(bundle),'characters':len(full),'figures':len(selected_charts),
                  'references':10,'tables':len(re.findall(r'^\*\*表 ',full,re.M))},ensure_ascii=False))
