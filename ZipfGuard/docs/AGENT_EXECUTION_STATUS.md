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
| A00 指纹与旧结果隔离 | 是 | `tests/test_research19_identity.py` | 否 | 否 |
| A01 19 站数据卡 | 是 | 由现有聚合报告生成，未重跑拟合 | 否 | 否 |
| A02–A12 | 否 | 否 | 否 | 否 |

A00 交付：`configs/research19/protocol.json`、`experiments/research19_manifest.py`。缓存键包含数据、分析源码、词表和拟合配置。`robustness-v2` 不能显示为当前方法结果。

A01 交付：`docs/DATASET_CARDS_19.md`、`reports/research19/data_audit.json`。LinkedIn 与 Ashley Madison 的频次语义待确认。上游去重仍是 unknown。没有把出现次数写成账户人数。
