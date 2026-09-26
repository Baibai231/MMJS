# 攻击适配器与协议边界

默认实验使用 open-minauto-v1。开放攻击与历史闭集接口分开，不能把旧候选清单排序结果接入新预算比较。

## 当前开放接口

core/open_attack.py 提供 frequency_stream、dictionary_stream、OpenNgram.generate、consume 与 evaluate_runs。experiments/open_pipeline.py 中 AttackEngine 负责训练、调参、策略知识和单次运行缓存。

生成器只接收 train、必要的 tuning、模型参数、公开策略条件和资源限制，不接收目标口令集合。候选先按允许的策略过滤，再在模型内去重、计费。即使不在真实数据中，候选也消耗预算。Min_auto 为逐口令最早命中的联合评价，不是最大单模型命中率。

PCFGAttacker.generate_open(train) 返回上游原始候选及停止状态。采用固定提交 b04bbdadfe8928fd1287fa73ad1aa46a297ff83a、纯 PCFG 模式、隔离临时训练目录；不使用目标允许列表。上游输出达到 raw_limit 时标记资源截断；主流水线再统一过滤、去重、计费并报告上游生成量及准备时间。

字符 n-gram 按长度先验与字符条件概率做 A* 枚举，使用精确后缀界减少展开，不采用 beam 内排序冒充全空间猜测次数。模型字符范围和长度上限仍是声明的支持边界。

必选攻击器失败终止；可选攻击器失败后在整次比较中移除并重算。记录安全错误类别，不公开第三方 stderr 中可能出现的口令。没有自动换成另一个模型的回退。

## 历史闭集接口

BaselineAttacker.fit_select_rank(train, validation, candidates) 与 RankingResult 继续服务 legacy 实验。旧 core/attackers.py、PCFG 的 fit_select_rank 和 CommandAttacker 仍接收公共候选；这些结果不是开放生成猜测预算。

CommandAttacker 的 command-jsonl-v2 输入包含 train、candidates、max_guesses、seed、metadata；外部命令逐行输出带 guess 的 JSON。适配器匹配公共候选并返回闭集排序。未来接入开放模型时必须另做目标独立性、计费与停止状态验收，不能直接复用闭集排名。

## 未接入的模型

PassLLM 的 runtime_status 只检查环境，integrated=false、participated=false；RFGuess 也尚未接入主协议。网页与报告明确显示这一状态。环境可用和实际参加实验是两件事，当前不下载其模型或伪造效果。

详细契约与研究边界见 [开放协议说明](../docs/OPEN_PROTOCOL.md)。
