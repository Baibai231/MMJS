# Agent 执行状态

> **后续核验与范围调整：**见 [19 数据集核验与下一阶段任务书](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/docs/PIPELINE_NEXT_PHASE_2026-09-25.md)。下表是旧 T00–T10 的交付状态，不代表全部科研实验已经完成；新主线按 A00–A12 验收。

> 对照三份任务文档。日期 2026-09-25。比赛名称、截止日期和提交格式仍是待确认，没有编造。

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| T00 发布保护 | 完成 | `experiments/evaluation_validity.py`，`tests/test_evaluation_validity.py` |
| T01 论文矩阵 | 完成 | `docs/PAPER_REPRODUCTION_MATRIX.md`，`docs/PAPER_AMBIGUITIES.md`，`docs/references.bib`，`paper_compatible` |
| T02 语料盘点 | 完成 | `docs/CORPUS_INVENTORY.md`，`configs/paper_reproduction/sites.json` |
| T03 分布 | 部分完成 | 带频次 RockYou 的拟合报告。六站点表 II/III 未齐 |
| T04 攻击器 | incomplete | PCFG 适配器保留原始序号。`ai/omen_adapter.py` 记录作者 OMEN 未接入，猜测数为 0 |
| T05 E1–E4 | incomplete | `experiments/paper_scenarios.py` 返回空命中率，不改名顶替 |
| T06 优化定义 | 完成定义，未证实 | `experiments/htpg_optimization.py`，`configs/optimization/method.json` |
| T07–T08 同成本与确认实验 | 未跑原场景 | 协议在 `docs/EXPERIMENT_PROTOCOL.md`。合成频次诊断不是确认集 |
| T09 证据与引用 | 完成边界内的文本 | 引用 R1–R8 见 `docs/references.bib`。技术结论以 `docs/PHASE_DECISION.md` 为准 |
| T10 决定 | 完成 | 假设未得到支持。见 `docs/PHASE_DECISION.md` |

验证：`python -m unittest tests.test_next_phase_contracts tests.test_evaluation_validity tests.test_htpg_baseline.HTPGMethodTests.test_paper_tie_rule_includes_equal_distance_and_blocks_shorter_length -q`

## 当前主线 A00–A12

| 任务 | implemented | tested | experiment_completed | claim_supported |
| --- | --- | --- | --- | --- |
| A00 指纹与旧结果隔离 | 是 | `tests/test_research19_identity.py` | 否。没有把旧表重标成新结果 | 否 |
| A01 19 站数据卡 | 是 | 由现有聚合报告生成 | 数据卡在，全量重拟合没有做 | 否 |
| A02 开放流与划分 | 是 | `tests/test_research19_pipeline.py` | hak5 的 occurrence 与 unique-disjoint 清单已写。19 站划分清单没有写 | 否 |
| A03 分布诊断 | 是 | 同上 | 摘要和两张 SVG 来自 JSON。不是新的逐站重拟合曲线 | 否 |
| A04 拟合比较 | 是 | `models_within_bic` 测试 | 7 个小站有 OLS。只有 hak5 做了三模型，而且是同一样本上的误差。12 站 incomplete | 否 |
| A05 切分尺度 | 是 | 同上 | hak5 子样本稳定性已写。没有 19 站，也没有攻击风险标签 | 否 |
| A06 特征 | 是 | 同上 | 19 站相关表仍是旧聚合。hak5 上有频次命中与特征的描述统计，不是因果 | 否 |
| A07 PCFG 与 OMEN | 是 | `reports/research19/hak5_smoke.json`，`transfer_hak5_hotmail.json` | hak5 预算 1000，以及 hak5→hotmail。不是 1e5，也不是 19 站 | 否 |
| A08 PassGPT / PassLLM | 是 | `reports/research19/neural_eval.json` | hak5 上各抽样 1000 条原始输出。PassLLM 唯一候选 968，预算 1000 为 incomplete。不是 DivideSearch，也不是 1e8 | 否 |
| A09 多模型主表 | 主表文件在 | 数字来自上面的 JSON | 否。FLA、PassGAN、PassFlow 没有进入已完成模型 | 否 |
| A10 两个假设 | 已写明，可被否定 | `docs/RESEARCH_HYPOTHESES.md` | 只有 hak5 探索。H1 这一站质量阈值并不更稳定。H2 等额并集没有超过验证集选出的单模型 | 否 |
| A11 确认实验 | 缺失单元已列出 | `reports/research19/confirmation.json` | 否。没有修改前/修改后对照 | 否 |
| A12 报告与复跑 | 是 | `docs/RESEARCH19_REPORT.md`，`python -m experiments.research19_rerun` | 否。比赛名称、截止日期和提交格式仍是待确认 | 否 |

`ai/omen_adapter.py` 的 `omen_status()` 仍表示这个函数本身不发猜测。hak5 的 OMEN 结果在 `reports/research19/hak5_smoke.json`，模式是 stdout、无成功反馈。不要把固定阶 Markov 叫成 OMEN。

A08 的 0 次猜中是 PassGPT 在 hak5 测试集 598 条上实际跑完预算 100 和 1000 的结果。PassLLM 预算 1000 没有写成 0，因为唯一候选只有 968。

2026-09-26 第二次核验之后，评价器把原始位置和唯一位置分成两根轴。序列比预算短时，除非明确是词典穷尽，否则该预算是 incomplete，不能写成 0。PassLLM 请求 1000 条时，作者代码会安排 1104 条轨迹，返回值是保留后再排序的结果，不是原始生成顺序。出现次数可以做加权统计，账户风险仍是 unknown。缓存键使用这次实际词表，并把 `core/counted_corpus.py` 算进分析指纹。旧协议图表写成 `*_historical.svg`，不再覆盖成当前结果。

2026-09-26 第三次核验之后，MAYA pickle 只去掉一条记录末尾的 LF 或 CR LF，并单独计数。值内部的控制字符整条排除，不删字符后继续计数。原始位置包含无效输出；有效输出位置另记。运行若被中断，终态是 interrupted，中断前已经发出的预算仍可计分。旧 schema 的图会拒绝生成，而不是画成空图。

2026-09-26 第四次核验之后，7 个小站按当前预处理重算，twitter 的分布和频次攻击使用同一身份。另外 12 个大站记为未重读。配额实验把总生成预算和唯一验证预算分开；跨模型重复不是 resource_truncated。纯中断通知不消耗生成次数。PassLLM 排序后保留候选的逐点 axis 是 sorted_retained_position。

A00 交付：`configs/research19/protocol.json`、`experiments/research19_manifest.py`。缓存键包含数据、分析源码、词表和拟合配置。`robustness-v2` 不能显示为当前方法结果。

A01 交付：`docs/DATASET_CARDS_19.md`、`reports/research19/data_audit.json`。LinkedIn 与 Ashley Madison 的频次语义待确认。上游去重仍是 unknown。没有把出现次数写成账户人数。
