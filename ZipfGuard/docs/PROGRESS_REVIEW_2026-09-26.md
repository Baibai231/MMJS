# ZipfGuard 进度核验：2026-09-26

> 核验提交：`7c5fa35`。对照上一轮 19 数据集任务书。检查源码、实际调用链、运行结果与数据卡；没有修改研究算法或运行大型攻击。
>
> **结论：A00/A01 有工程进展，但尚未通过完整验收；A02–A12 尚未实现。真实多模型攻击与创新确认实验没有新增结果。**

## 1. 本轮确实推进的内容

| 改动 | 核验结论 |
| --- | --- |
| 新增 research19 协议配置 | 固定了 19 站范围，说明不再以补齐原论文缺失站点作为前置条件 |
| 扩充 robustness 指纹 | 已加入评价模块、指纹代码和默认词表等文件，修复了上一轮指出的部分缺口 |
| 新缓存格式 | 升为 `maya-aggregate-v3`，包含部分分析源码、默认词表和拟合配置 hash；真实调用仍有缺口，见下文 |
| 文件头读取 | 改为只读取 16 字节，消除了为识别格式先加载整个文件的问题 |
| 19 站数据摘要 | 已生成 Markdown 和 JSON；明确 occurrence 不等于已核实的账户人数 |
| 进度表 | 分开标注 implemented/tested/experiment_completed/claim_supported，A02–A12 如实为否 |

测试：本次运行 `test_research19_identity`、`test_evaluation_validity`、`test_next_phase_contracts`、`test_external_maya`、`test_maya_frequency`，共 **19 项通过**。这不是全量测试或科研实验完成证明。

## 2. 阻碍验收的具体问题

### P1-1：更换实际词表仍可能复用旧 IGR 缓存

位置：

- [research19_manifest.py](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/experiments/research19_manifest.py:11)，11、37–38、56–65 行。
- [external_maya_validation.py](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/experiments/external_maya_validation.py:148)，148–151 行；CLI 在 194–202 行接受实际词表。

原因：`lexicon_sha256()` 固定哈希 `resources/htpg_reference_v1.json`。CLI 可以通过 `--lexicon` 加载另一份词表，但 `_load_cache()` / `_save_cache()` 没有收到实际 extractor 或词表身份。

本次在临时目录、人工口令样本中复现：

1. 用词表 A 运行 `_one_site()`，生成缓存。
2. 保持数据不变，换词表 B 运行。
3. 第二次 `analysis_reused=True`，得到 `word_type` 的 IGR `0.2303466122545442`。
4. 按词表 B 直接重算，该值应为 `null`，两份结果不同。

影响：以后扩展多语言词表或做词表消融时，可能把旧结果错归给新设置。

验收要求：把本次实际使用的词表内容/规范化后配置身份传入缓存键；测试必须走真实 `_one_site()` 的 A→B 调用链，确认词表变化导致 miss，重新计算后再次运行才 hit。只测试一个独立 hash helper 不足以证明主路径正确。

### P1-2：历史结果标记尚未接入图表生成路径

位置：

- [research19_manifest.py](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/experiments/research19_manifest.py:81)，81–88 行。
- [build_figures.py](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/experiments/build_figures.py:94)，94–159 行。

`historical_report_status()` 当前只有测试调用，没有被实际图表生成器使用。图表生成器仍直接读取旧 JSON，不校验协议、输入身份、发布状态和对应源码。

本次把现有聚合 JSON 复制到临时目录，执行真实 `build_figures.main()`：

- 输入 grid 是 `robustness-v1`。
- 退出码 0，成功生成 `budget_comparison.svg`。
- 图中没有历史警示，也没有协议版本。

已有 `freeze_directed_table.py` 的源码 hash 拒绝逻辑仍在，不能把某条表格路径受保护推广成全部图表已隔离。

验收要求：统一图表、表格、报告的读取入口；旧结果只能显式进入历史展示并带版本/警示，或者拒绝生成当前结果。主指标不可发布、预算未完成时禁止渲染成正常数值。

### P2-1：缓存源码指纹仍未覆盖实际解析依赖

位置：[research19_manifest.py](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/experiments/research19_manifest.py:12)，12–20 行；[occurrence_frequency.py](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/core/occurrence_frequency.py:17)。

分析代码依赖 `core/counted_corpus.py` 的行解析和 LFS 检查，但该文件不在 `ANALYSIS_SOURCES` 中。更改解析规则可能改变计数，却不会因这项变化使旧分析缓存失效。

当前 cache identity 模块自身也未计入分析源码清单。配置 hash 来自固定字典，不能据此声称已覆盖未来任意实验配置。

验收要求：从实际依赖和调用参数生成分析身份；至少将解析代码与实际拟合/预处理配置纳入。验证关键依赖变化时缓存失效，避免仅扩充一个人工清单后停止核对。

### P2-2：已有绘图函数让不同数值曲线使用不同坐标尺度

位置：[build_figures.py](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/experiments/build_figures.py:9)，9–24 行及 105–108 行。

`_polyline()` 按每条曲线各自的最小值/最大值缩放，预算比较循环逐条调用。人工曲线 `(0.01, 0.02)` 和 `(0.1, 0.2)` 的输出像素坐标完全一致，会掩盖实际十倍差异。

这是已有问题，本轮没有新引入。当前脚本后面还会用另一个柱状图覆盖同名 `budget_comparison.svg`，使预算曲线与柱状图产物身份混淆。

验收要求：比较图共享坐标轴；命中率使用明确的统一数值范围；预算曲线和单预算柱状图分别命名；用已知大小关系的人工数据检查图形编码。

### P2-3：pickle 字符串被静默移除换行字符

位置：[occurrence_frequency.py](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/core/occurrence_frequency.py:117)。

pickle 内已经是完整字符串，读取代码仍对每条值执行 `strip("\n")`。本次人工输入 4 个不同字符串，其中一对仅相差结尾换行，解析后变成 3 类，最高频次变为 2；没有丢弃或规范化记录。

这与文本文件读取时移除行分隔符是不同的情况。19 份真实文件是否含受影响记录尚未量化，**本次没有据此判定已有真实统计错误**。

验收要求：为每种输入格式定义明确的字符串保留/过滤规则。若排除控制字符，记录排除数量；不要静默改变值后计数。拟合和攻击必须共用同一口令身份规则。

## 3. A01 数据卡：摘要已生成，来源审计未完成

已确认：

- 19 个 payload 都存在，字节数与记录匹配。
- 新 `data_audit.json` 的总量、不同字符串数量和重复率与原聚合报告一致。
- 本轮对 myspace、hotmail、faithwriters、hak5、singles、twitter 六个小站重新读取计数并计算 SHA-256，均匹配。
- 未重跑其余大型数据的全部拟合与特征分析。

尚缺：

- 来源代码 commit、具体格式/编码、预处理版本。
- 原始/有效/跳过行数、单次出现比例、长度与字符范围。
- 上游格式化、去重和过滤依据；当前 19 站 `upstream_dedup` 均为 unknown。
- 固定训练/验证/测试 split 及其 hash。
- 跨站重叠和公共模型历史训练暴露记录。
- 生成数据卡的可复跑脚本、输入聚合报告 hash、生成命令和环境记录。

LinkedIn 与 Ashley Madison 的限制已写出，是正确的边界记录。但记录 unknown 并不意味着相关来源问题已经解决。

**A01 应标为“摘要交付完成，数据验收部分完成”。**

## 4. 攻击与创新实验没有新增推进

攻击源码及主要适配器与上一轮一致。`reports/research19/` 只有 `data_audit.json`，没有攻击矩阵或确认实验。

| 模型/环节 | 当前真实状态 |
| --- | --- |
| PCFG | 上游和可运行适配器存在；默认 20,000、硬上限 1,000,000；整段 stdout 读取、公共候选过滤，尚未形成 19 站开放流实验 |
| OMEN | `integrated=False`，猜测数 0 |
| PassLLM | 调用评测仍抛“尚未接入”；当前配置底座/LoRA 路径不存在；本次 Python 缺 torch/transformers/peft |
| PassGPT | 没有对应 adapter 或实际结果 |
| 流式攻击合同 | `core/attack_stream.py` 尚未实现 |
| 新分布/拟合/切分实验 | 对应 diagnostics/fit benchmark/split stability 驱动器尚未实现 |
| 风险解释与确认实验 | 没有 feature stability、attack matrix、confirmation 产物 |

PCFG 上游版本目前依据本地 `UPSTREAM_VERSION.txt` 声明；目录没有上游 `.git`，不能把 manifest 匹配说成代码内容已被完整验证。

## 5. 下一轮优先顺序

1. **完成 A00 验收**：先修实际词表缓存、分析依赖指纹和图表历史隔离。用真实调用链回归，替换只验证辅助函数的测试。
2. **补 A01 可复跑审计**：优先解释 LinkedIn/Ashley Madison 来源处理，补计数与格式字段，提供生成脚本。
3. **开始 A02**：固定真实语料 split、统一原始生成/去重校验/耗时计数，分离生成器和评价器。
4. **并行推进 A03 与 A07/A08**：分布尺度诊断；PCFG/作者 OMEN；PassGPT/作者 PassLLM 小预算实际运行。
5. **再验收 A09**：第一份真实多模型矩阵。没有生成日志、有效预算和目标命中结果，不计为模型接入完成。

下一次最值得检查的交付是：**真实数据 split manifest + 开放生成接口 + 至少一条可复跑的真实数据攻击链路**。同时推进分布诊断，避免全部投入继续写状态文档。

## 6. 本次证据与边界

- [可复现检查结果](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/reports/review_2026-09-26_checks.json)：词表缓存、历史图表、坐标缩放、pickle 规范化。
- 所有构造样本为人工数据，临时图表写入临时目录，没有覆盖现有报告图。
- 本次只增加核验文档与聚合检查记录，未修改业务代码。
- 研究主目标与详细 A00–A12 任务仍见 [下一阶段任务书](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/docs/PIPELINE_NEXT_PHASE_2026-09-25.md)。
