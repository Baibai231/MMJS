# 第五次核验：上轮主要修复通过，汇总与复跑链路尚未收口

日期：2026-09-26。对象：`f1cb600` 加当前未提交修改。新增本报告及 `reports/review_2026-09-26_v5.json`；没有改业务代码、覆盖原实验报告或重新运行神经推理。

## 结论

上一轮四项主要问题可以确认有实质修复。40 项相关测试通过；在临时输出目录独立执行新增预处理脚本和真实配额脚本，两份结果都与保存 JSON 完全一致。当前不足主要是新结果没有贯通全部消费者和复跑入口，科研范围也仍是小预算探索。

## 实际运行确认

运行 `research19_preprocess_identity.main()`：7 个小站按当前预处理重新读取并拟合；Twitter 39487 条、35106 类、排除 31 条，与保存结果一致。脚本还重新计算了 Twitter 的频次攻击和特征排序。新分布摘要使用这些新计数与拟合结果，保留旧值为历史字段，另 12 站明确标为未重读。不能把 7 站重算推广成 19 站来源语义均已确认；上游行结束符定义仍记为 not_independently_audited。

运行 `research19_quota.main()`：验证集选择频次基线，验证命中频次 91、OMEN 27、PCFG 63；测试单模型 127/598。三个模型各取原始前 300 条，总生成 900 条，跨模型重复 79 条，唯一候选 821，命中 93/598。生成完成，唯一验证预算 900 未达到，两者已正确分开。人工重复候选反例也不再把重叠判作资源截断。这只是等额配额探索，不是分布驱动配额优化或 H2 冻结确认。

纯中断通知现在不计入原始输出；一条输出后收到通知，原始计数为 1、预算 2 未完成。显式 failed_emission 则另外消耗一次原始位置。PassLLM 保存产物的逐点 axis 已为 sorted_retained_position，原始生成位置仍为 null，避免误读为等原始生成成本。

## P2：主表仍保留已被修正的旧配额结果

当前 `reports/research19/quota_hak5.json` 已有 total_generation_budget、generation、unique_verification_budget 等新字段。但 `reports/research19/attack_matrix.json` 的 quota_exploratory 仍使用旧 test_equal_share_union，写着 resource_truncated、cracked=null；单模型轴也还是旧 unique_position。

这不是新实验数值跑错，而是主表没有刷新。使用主表制作答辩材料仍会得到与专项报告矛盾的解释。应提供确定性的主表构建入口，从当前专项结果生成，并校验 schema、输入身份与完成状态；不要手动更新几处数字代替整个汇总。

## P2：复跑入口没有执行新的身份重建

`experiments/research19_rerun.py` 第 14–22 行先跑 distribution_diagnostics，但没有在它之前跑 research19_preprocess_identity。新增身份文件因此仍依赖单独执行。

本轮还验证：当 identity 文件缺失，summarize_audit 会退回历史数据，preprocess_identity=null，却将 sites_not_rescanned 返回为空列表。虽然还有历史提示字段，但这个列表不能表达真实的未重读范围。应先重建或严格验证身份文件；缺失时明确失败或标出全部未确认站点，不能让“复跑完成”掩盖身份缺失。

## P2：重复率图没有展示新旧统计的区别

当前分布 JSON 已标清 7 站新预处理、12 站历史未重读；绘图器却只按 occurrence_weighted_metric_available 筛选，不使用 count_source。实际临时目录绘图得到 17 根柱子，其中 7 根来自本轮预处理、10 根来自历史未重读统计，SVG 没有历史标记。

这张图可以展示混合来源的描述性摘要，但必须标明来源，不能作为“19 站统一新预处理后的对比”。建议分图或显式图例，且宏观均值说明采用哪个集合。

## 后续目标

先把身份重建、诊断、专项结果、主表和图表串成有依赖检查的流程，修复上述三处收尾问题。随后可以进入多站点、多种子及预算扩展，继续研究分布、拟合与攻击难度的联系。

当前确认实验仍 incomplete；19 站多模型、高预算、PassLLM DivideSearch、修改前后及 frozen/adaptive 尚未完成，未发现新增创新成立证据。现阶段准确结论是“上轮关键逻辑修复通过，研究基础更可靠；完整科研任务未完成”。
