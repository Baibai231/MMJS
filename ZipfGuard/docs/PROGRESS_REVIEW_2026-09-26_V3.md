# 第三次核验：部分修复成立，但真实数据入口出现回归

核验日期：2026-09-26。对象：`8c98a9d` 加当前未提交修改。只新增本审计及聚合检查记录，没有修改研究代码或原实验报告。

## 总体结论

本轮主要是修复评价、身份和绘图，而非新增科研实验。35 项相关测试通过，但不能验收为修复完成：真实 MAYA 小站的读取入口已经被新的过滤规则阻断。上一轮的模型运行结果仍是历史快照，不能代表当前工作区可以复跑。

## P1：真实数据全部被过滤，核心实验无法开始

`core/occurrence_frequency.py` 第 120–122 行新增规则：只要字符串包含控制字符，就丢弃整条记录。独立检查发现，以下 7 个小站的所有记录均带末尾 LF；逐站调用实际 `load_occurrence_counter` 全部报“有效类别少于 3，不能拟合”。

| 站点 | 原始记录数 | 带末尾 LF 的记录数 |
| --- | ---: | ---: |
| hak5 | 2984 | 2984 |
| hotmail | 9813 | 9813 |
| myspace | 41545 | 41545 |
| faithwriters | 9709 | 9709 |
| singles | 16248 | 16248 |
| twitter | 39518 | 39518 |
| phpbb | 255420 | 255420 |

hak5 文件 SHA-256 与本地 ready.json 一致，文件没有被本次测试改写。问题来自解析规则，不是文件损坏。由于模型脚本和拟合脚本都依赖该入口，本轮未继续运行神经推理；它们在生成开始前就会失败。

上一轮要求避免静默改变字符串，不意味着应不查实际格式就排除所有换行记录。修复必须先核实 MAYA 上游序列化是否保留文本行结束符，按确定的来源规则处理末尾分隔符，并区分内部控制字符。记录规范化数量、排除数量和预处理版本；不能只把过滤改回宽泛 strip 后结束。加入真实格式的回归样本，至少确保这 7 站均可读取，再跑 hak5 全链路。

## P1：新 raw_position 仍不是原始生成位置

`core/attack_stream.py` 第 131–143 行对每条记录增加 raw，但无效输出在进入 raw_order 前被跳过，第 163 行又用压缩后的 raw_order 计算位置。

本轮人工反例：100 条无效输出之后才出现目标。结果 raw_emissions=101，却在 raw_position 预算 1 报命中；预算 101 反而报 incomplete。这说明当前“原始轴”实际上是有效输出轴。若保留这个轴，应明确命名 valid_position，同时另外保存包括无效输出成本的原始位置。

中断状态也有不一致：只有传入 completion=unspecified 才将中断提升为 interrupted。传入 reached_budget 后遇到中断标记，最终同时输出 interrupted=true 和 completion=reached_budget。可以保留中断前已完成预算的有效结果，但需要区分整个运行终态与各预算点状态，不能互相矛盾。

## P1：源码已更新，现有产物仍旧，复跑链路没有完成迁移

现有 `neural_eval.json` 仍含 requested_raw_emissions 和 points_all_test_rows，没有新代码的 scheduled_trajectories、points_raw_position 等字段；`hak5_smoke.json` 的 OMEN 也没有新轴字段。旧 `distribution_diagnostics.json` 仍将 17 站标成 account_risk_applicable=true，而现在重新调用摘要函数得到 0 站。

这个差异已经影响真实下游：将现有 JSON 复制到临时目录，运行当前 `research19_charts.main()`，重复率图生成成功但柱子数量为 0。原因是新绘图器要求 occurrence_weighted_metric_available，旧 JSON 没有它；程序静默跳过全部数据。小预算复跑入口又没有先重新生成 distribution_diagnostics。

应在数据入口修好后重建 split、诊断、攻击、主表和图表，写入产物版本及源码/预处理身份。消费者发现旧 schema 应明确拒绝或执行有记录的迁移，不应静默生成空图。当前不能把文档中“评价器已经分轴”理解为已存实验结果全部完成迁移。

## P2：历史图表保护只覆盖部分入口

预算曲线已共享 0–1 纵轴，曲线与柱图已分别命名，已知历史协议也会生成历史文件名。这些是有效修复。

但 `experiments/build_figures.py` 的 head_shift 分支仍直接生成 head_shift.svg，不检查协议。本轮在临时目录输入 protocol=robustness-v1，真实 main() 成功生成无历史警示的图。Pareto 分支虽然使用历史文件名，却丢弃了历史警示文本。还需要统一入口，并在展示前检查可发布性，而不是只测试文件命名辅助函数。

## 已确认有效的修复

实际词表身份已传入 `_one_site` 的缓存读取与写入；测试真实执行了换词表 miss、相同词表再次 hit。分析指纹加入 counted_corpus 和身份模块，缓存版本升为 v4。split 指纹改为字符串与频次的 JSON 序列化，频次丢失与换行拼接碰撞的测试通过。账户语义在新摘要函数中明确为 unknown。短序列与词典耗尽已分开处理，重复输出与唯一输出也已分轴。PassLLM 新代码披露 1104 条计划轨迹，并注明排序后验证顺序不是原始生成顺序。小站拟合在已知数据卡超过上限时会提前跳过全量读入。

这些进展应当保留，但它们的验收范围是相关源码路径和测试，不能扩展成全部实验产物已更新。

## 接下来的验收顺序

首先恢复真实数据读取，并确认处理的是来源格式而不是任意删除口令字符。随后修正原始位置和中断状态，使用无效输出、重复、短流、正常耗尽等反例验证。之后重建现有小实验及全部下游产物，确保同一轮输入、代码和报告一致，补上真实绘图入口的历史保护。

完成这些之后，才回到 19 站、多种子、更高预算与创新确认。当前 confirmation.json 仍 incomplete，修改前后和 frozen/adaptive 尚未完成；没有新增证据支持创新成立。

## 证据范围

运行测试：test_research19_pipeline、test_research19_identity、test_evaluation_validity、test_distributions、test_external_maya、test_maya_frequency，共 35 项通过。独立检查结果保存在 `reports/review_2026-09-26_v3.json`。真实数据只做上述 7 站读取与聚合检查，不输出口令；图表反例在临时目录执行，未覆盖原图。
