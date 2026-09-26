# 阶段 0 交付：C01–C06

| 字段 | 填写内容 |
| --- | --- |
| 任务 ID / 负责人 | C01–C06；当前 agent |
| 起始版本 / 最终版本 | 起始 `f1cb600`。最终版本是包含本文件的提交。 |
| 目标与本次范围 | 让当前小实验的主表、分布摘要和图从同一批 JSON 生成。没有开始 19 站重读、高预算或确认实验。 |
| 依据 | `docs/NEXT_STAGE_MASTER_CHECKLIST_2026-09-26.md` 阶段 0。配额口径来自第四次核验：生成 900 已完成，唯一验证 821，命中 93。 |
| 输入身份 | `reports/research19/preprocess_identity.json` 与 hak5、频次、迁移、配额、神经实验 JSON。预处理版本 `maya-pickle-record-terminator-v1`。 |
| 改动说明 | 新增 `experiments/research19_attack_matrix.py`。复跑入口分成 `report` 和 `full`。身份文件缺失时不再把未重读站数写成空列表。重复率图用颜色区分 7 个当前站和 10 个历史站。 |
| 运行方式 | `python -m experiments.research19_rerun --flow report`。神经实验不在此流程中，命令是 `python -m experiments.research19_neural_eval`。 |
| 验证结果 | `tests.test_research19_pipeline` 与 `tests.test_research19_identity`。临时目录只放入专项 JSON 后，主表仍是生成 900、唯一 821、命中 93。缺少身份文件时分布摘要入口退出。 |
| 核心产物 | `reports/research19/attack_matrix.json`，`distribution_diagnostics.json`，`figures/repeat_ratio.svg`，`configs/research19/model_checksums.json`，`requirements-research19.lock.txt`。 |
| 四状态 | C02、C04、C06 的脚本和锁文件已有提交 `35b7031`。C01、C03、C05 在该提交里还没有拦住身份不匹配和预算误标；随后的工作区修改用 `build_matrix` 拒绝这两种输入。claim_supported 仍为否。 |
| 对照与结论 | 生成预算 900，`reached_budget`。唯一验证 821，预算 900 的 cracked 为 null。821 条上命中 93/598。这不是方法优势。 |
| 缺失与失败 | 12 个大站未重读。PassLLM DivideSearch、1e5 以上预算、修改前后确认都没有做。 |
| 下游更新 | 主表、分布摘要、两张 research19 图已按当前 JSON 重建。 |
| 下一依赖 | 阶段 1 的 R01–R04 与 D01–D06。D05 仍卡在 12 个大站的可扩展读取。 |
