# ZipfGuard 研究依据与引用映射

> **新增研究方向的文献：**分布、拟合、PassLLM、PassGPT、MAYA 与评价依据已在 [最新任务书第 8 节](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/docs/PIPELINE_NEXT_PHASE_2026-09-25.md) 补齐。实施优先级以该任务书为准；下面保留原 R1–R8 引用记录。

> 核查日期：2026-09-25。以下使用原论文、出版社、会议或作者原始来源。引用用途是支持问题定义、基线和评估设计，不代表这些论文已经证明 ZipfGuard 的改进有效。
>
> 主任务见 [Agent 执行任务书](/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/docs/AGENT_NEXT_PHASE_TASK.md)。用户要求参加全国密码竞赛并使用多篇论文支撑；比赛届次、官方格式与具体截止日期尚未确认。

## R1：必须复现的基线

**Yang Xiao, Jianping Zeng. Dynamically Generate Password Policy via Zipf Distribution. IEEE Transactions on Information Forensics and Security, 17:835–848, 2022. DOI: 10.1109/TIFS.2022.3152357.**

- [出版标识](https://doi.org/10.1109/TIFS.2022.3152357)
- [作者机构出版记录](https://faculty.fudan.edu.cn/zengjianping/zh_CN/zdylm/644281/list/index.htm)
- 本地 PDF：`/Users/cjx_main/Desktop/new/Dynamically_Generate_Password_Policy_via_Zipf_Distribution.pdf`；本次读取了实验设置并核对第 8 页图像。

**用于：**HTPG 的 Zipf 曲率头尾划分、九特征排序、头部建议；真实语料上的跨站 PCFG／OMEN 评估；修改前后头部、特征排序和迭代分布实验。

**不能直接用于：**宣称本项目已经取得原论文的攻击降幅或用户记忆率；将替代词表、Markov substitute 和合成 40 次预算说成完整原场景复现。

**必须记录的原文歧义：**表 I 列六个数据集，IV.A.1 却写其余四个训练集；图 7／9 又区分不同训练集的范围。后续须固定训练—测试配对解释，不擅自补成唯一“原始设置”。

## R2：PCFG 攻击基线

**Matt Weir, Sudhir Aggarwal, Breno de Medeiros, Bill Glodek. Password Cracking Using Probabilistic Context-Free Grammars. IEEE Symposium on Security and Privacy, 391–405, 2009. DOI: 10.1109/SP.2009.8.**

[IEEE 会议论文](https://conferences.computer.org/sp/pdfs/sp/2009/oakland2009-23.pdf)

**用于：**概率语法攻击器的训练与生成基线；将结构规律纳入猜测评估。工程上应记录使用的实现版本，并保留实际猜测预算。

**不能直接用于：**把某个后续实现版本视为完全等同 2009 算法，或为 ZipfGuard 的新建议策略提供有效性保证。

**对应任务：**T04、T05。

## R3：OMEN 原始攻击器

**Markus Dürmuth, Fabian Angelstorf, Claude Castelluccia, Daniele Perito, Abdelberi Chaabane. OMEN: Faster Password Guessing Using an Ordered Markov Enumerator. ESSoS 2015, LNCS 8978:119–132. DOI: 10.1007/978-3-319-15618-7_10.**

- [Springer 原始出版页](https://link.springer.com/chapter/10.1007/978-3-319-15618-7_10)
- [作者实现 RUB-SysSec/OMEN](https://github.com/RUB-SysSec/OMEN)

**用于：**接入论文使用的 Markov 有序猜测基线，独立检验策略是否只是针对 PCFG 的特征偏好。

**实现注意：**作者仓库说明，模拟明文攻击模式可使用成功反馈调整长度调度，文件／stdout 模式没有相同反馈。后续任务应明确选择和记录模式；不能让攻击器无声明地使用测试成功反馈。

**不能直接用于：**将普通 n-gram 排序或固定阶束搜索替代器称作 OMEN。

**对应任务：**T04、T05、T08。

## R4：策略优化与用户响应建模

**Jeremiah Blocki, Saranga Komanduri, Ariel D. Procaccia, Or Sheffet. Optimizing Password Composition Policies. ACM EC 2013.**

[作者公开论文 arXiv:1302.5101](https://arxiv.org/abs/1302.5101)

**用于：**将“策略改变口令分布”和“用户响应假设”写入明确的优化问题；将有限猜测预算下的成功概率作为目标之一。

**不能直接用于：**宣称本文计划的成本约束、联合建议或启发式选择器继承该论文的近似最优保证。两者的策略空间和响应模型需要单独比较。

**对应任务：**T06、T07、T09。

## R5：动态策略与口令多样性

**Sean M. Segreti et al. Diversify to Survive: Making Passwords Stronger with Adaptive Policies. SOUPS 2017, 1–12.**

[USENIX 原始出版页与全文](https://www.usenix.org/conference/soups2017/technical-sessions/presentation/segreti)

**用于：**解释为何需要检查策略诱导的新集中模式、结构多样性，以及安全与使用负担的关系。

**不能直接用于：**把本项目模拟采用率、编辑距离或多样化后缀当作真实用户研究；也不能声称首次提出动态口令策略。

**对应任务：**T06、T07、T08。

## R6：多攻击器评估与偏差

**Blase Ur et al. Measuring Real-World Accuracies and Biases in Modeling Password Guessability. USENIX Security 2015, 463–481.**

[USENIX 原始出版页与全文](https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/ur)

**用于：**要求攻击器有合理配置，报告分项结果，避免只用一个猜测器判断修改特征的安全性。

**不能直接用于：**把多个未调好的攻击器组合称为充分模拟现实攻击，或把所有攻击器的预算随意相加后保持原预算标签。

**对应任务：**T04、T07、T08。

## R7：可选的神经猜测验证

**William Melicher, Blase Ur, Sean M. Segreti, Saranga Komanduri, Lujo Bauer, Nicolas Christin, Lorrie Faith Cranor. Fast, Lean, and Accurate: Modeling Password Guessability Using Neural Networks. USENIX Security 2016, 175–191.**

[USENIX 原始出版页与全文](https://www.usenix.org/conference/usenixsecurity16/technical-sessions/presentation/melicher)

**用于：**在 PCFG／OMEN 主实验完成后，评估是否需要一种独立模型族检查方法泛化。

**不能直接用于：**仅凭模型概率宣布实际猜测排名；若采用猜测数估计，需标注估计方法及误差。该任务不是首轮复现的前置条件。

**对应任务：**T08 的可选扩展。

## R8：自适应攻击与评估偏差扩展

**Dario Pasquini, Marco Cianfriglia, Giuseppe Ateniese, Massimo Bernaschi. Reducing Bias in Modeling Real-world Password Strength via Deep Learning and Dynamic Dictionaries. USENIX Security 2021, 821–838.**

[USENIX 原始出版页与全文](https://www.usenix.org/conference/usenixsecurity21/presentation/pasquini)

**用于：**设计独立于原论文固定跨站攻击的策略知情／自适应压力测试，检查修改后的模式是否容易被重新学习。

**不能直接用于：**把攻击者获知目标测试口令当作合法调参，或把该论文的结果直接用于本项目的性能宣称。

**对应任务：**T08。

## 引用验收规则

1. 任务书中的 R 编号是本项目引用键；提交前按比赛指定格式排版，核对 BibTeX 元数据。
2. 正文至少形成四类引用关系：HTPG 主线、策略优化动机、用户响应／多样性、攻击评估。
3. 每条创新主张同时列出继承来源、与前人的差异、对应实验。仅有引用数量不构成创新证据。
4. 对只核过摘要或出版信息的辅助论文，在依赖其具体算法、定理或实验设置前阅读全文。
5. 原论文给出的数值属于文献结果；本项目结果只能来自自己的版本化实验产物。
