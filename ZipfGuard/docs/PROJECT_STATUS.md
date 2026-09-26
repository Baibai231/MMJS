# ZipfGuard 项目状态

更新日期：2026-09-26。

## 当前默认主线

已按 [论文主方案](../DP_HTPG_AI_竞赛详细方案.md) 完成默认代码、命令行、轻量网页、Streamlit、报告和配置的联合迁移。主线为真实带频次数据、开放候选、逐口令 Min_auto、显式用户响应及风险—成本推荐。模块映射和协议细节见 [OPEN_PROTOCOL.md](OPEN_PROTOCOL.md)。

- 全文件严格解析与出现抽样；train/tuning/validation/test 隔离，权重守恒。
- 四类开放攻击器：训练频次、字典变形、字符 n-gram、PCFG；策略过滤与去重后计费，目标外猜测也计费。
- R0 解析重新选择、有限重试、R1 修补、R2 混合与放弃；成本保留全体初始用户分母。
- F/A0/A1 评价，主终点为 A1 Min_auto；验证选策略并冻结后才评价测试集。
- 常见规则系列、训练频次特征发现、Pareto、按需求约束推荐、配对区间以及无可行/无改善证据状态。
- 两种界面共用核心与展示；参数、图表、下载、预算缺失及失败状态均已切换。
- 多种子、响应、预算与等候选规模消融的批量入口；计划在读取数据前保存。

## 论文 HTPG 基线与并行研究模块

带频次解析也保留在 core/rockyou.py 与 core/counted_corpus.py；Git LFS 指针会直接报错。论文 PDF-Zipf 回归和曲率切分在 core/htpg_fit.py，九类特征、等权 IGR 及只对 HeadSet 输出的逐口令建议分别位于 core/htpg_features.py、core/htpg_igr.py、policy/htpg_generator.py。这些是论文复现与对照模块，不替代 open-minauto-v1 默认推荐流程。

~~~powershell
python -m experiments.reproduce_htpg_baseline --input ..\rockyou-withcount.txt --output reports\htpg_rockyou_fit.json
python -m experiments.reproduce_htpg_baseline --input ..\rockyou-withcount.txt --output reports\htpg_rockyou_features.json --with-features
~~~

experiments/suggestion_compare.py 比较论文等权 IGR、验证集预算/修改率排序和“训练集头部黑名单或长度小于 8”的现代基线。已有合成结果中现代基线不低于新方法，部分种子的配对区间不支持改善；这些结果不是真实口令上的防御结论。

独立 MAYA 站点仅用于聚合拟合和特征迁移，明文不进入报告，也不回写已冻结的策略权重。它尚未进入 open-minauto-v1 的推荐证据链。

## 同预算合成对照

python -m experiments.robustness_protocol 运行支持集 652 项的历史对照。生成器、公开候选和排名字段分开；马尔可夫替代保留原始生成序号，并明确不是论文 OMEN。

较早报告中的正收益受闭集候选泄漏影响，不能作为结论。robustness-v4 使用同一个执行器枚举改写，漏收特征不允许发布；当前保留零收益和负结果。历史 576 项流程仍会在高预算饱和，只能作为旧基线，通过 tools/legacy_demo.py 或网页 /legacy 运行。

## 本轮实际验收证据

| 检查 | 结果 |
|---|---|
| 合并后开放主线定向回归 | open protocol、open delivery、delivery、PCFG adapter 共 49 项全部通过，10.335 秒 |
| 合并后全量回归预检 | 118 项通过；2 项 research19 摘要测试因缺少未跟踪生成产物 `reports/maya_external_validation.json` 与 `reports/research19/data_audit.json` 报 `FileNotFoundError`；末项计算密集型稳健性测试长时间未完成，未计为通过 |
| 真实文件读取 | 14,344,391 行；有效出现 32,603,039；排除 10 行、349 次出现 |
| 快速三模型，真实 2,000 次出现抽样 | 19 个验证策略中 18 个可完整评价；complex-12 因接受质量为零不可行 |
| 快速运行的主要预算 | 每模型 1,000 次；实验计算约 14.852 秒，不含外置数据加载时间 |
| 四模型含必选 PCFG，三种响应 | R0/R1/R2 共 18 项验证评价完整，模型失败 0；约 32.932 秒，不含复用数据加载 |
| Edge 153.0.4234.48 | 400 次真实出现、三个模型、完整运行及下载通过；JavaScript 错误 0 |
| 批量入口 | 种子 11/23 × 分布引导/等规模消融，共 4 次真实数据运行完成，模型集合一致 |
| 命令行重放 | 划分哈希、选择哈希、验证风险及候选流哈希与对应批量运行一致 |

机器可读证据保存在 reports/open_real_checks.json、reports/open_browser_checks.json、reports/open_tests.log、reports/open_replay_checks.json、reports/study_acceptance/summary.json 和同目录报告中；生成结果不进入版本控制。

本次 2,000 次出现的快速运行按三项需求分别选出 deny-keyboard_walk、block-8、baseline，三项均为 no_supported_improvement。小样本工程验收没有证明推荐策略优于常见规则，也没有为了展示改动需求阈值。

## 当前实现的明确边界

M1 已使用训练频次头部与特征评分影响候选搜索；交叉拟合攻击标签尚未启用。R1 是固定简单修补序列，不是全局最小编辑距离求解器。默认风险主目标只优化指定主要响应和 A1；A0 与其它响应单列，尚未提供跨情景最坏风险优化或 A2。

全文件源哈希和严格读取已核验；来源真实性、全局重复字符串审计和独立用户语义尚未验证。样本内相同字符串已合并。模型范围有限，n-gram 的字符/长度边界与所有资源截断明确记录。

Bootstrap 固定训练和选中策略，区间为逐点探索性比较；正式规模、多种子与多重比较设计仍需按冻结计划执行。响应概率、需求阈值与成本代理没有真实用户研究校准。

尚未完成论文 OMEN 本体、PassLLM、RFGuess、A2、真实用户研究、第二台干净机器复跑和比赛提交格式核验。固定阶马尔可夫必须继续标为非 OMEN。MAYA 等外部聚合证据不能直接推出跨站猜中率或防御收益。

## 历史记录

2026-09-22 的 43 项工程测试与旧闭集实验见 [第一大点验收报告](SECTION1_ACCEPTANCE.md)。历史入口保留 /legacy、web/legacy_app.py、tools/legacy_demo.py；它们不能代表新的开放协议结果。
