# 口令分布与动态干预文献核查

核查日期：2026-10-10。用途：ZipfGuard 报告“背景分析”和“相关研究”。以下是按课题相关性筛选的核心文献，并非对所有论文的穷尽检索。链接指向论文原文、作者存档或出版方页面；中文概括是对原文的归纳，不应写成作者原话。

## 先读的六篇

| 文献 | 已完成的工作 | 与本项目的界线 |
| --- | --- | --- |
| Schechter, Herley, Mitzenmacher, 2010, [*Popularity is Everything*](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/popularityISeverything.pdf) | 用持续更新的口令流行度近似计数拒绝过热新口令，也提出让使用热门口令的已有账户重选。 | 已覆盖“依据群体频次处理存量账户”的基本思想；未给出本项目这种固定通知预算下的逐轮群体、规则、规模联合选择。 |
| Segreti 等, 2017, [*Diversify to Survive*](https://www.usenix.org/conference/soups2017/technical-sessions/presentation/segreti) | 随新口令创建而改变组成要求，并用 2619 人的实验考察安全性与可用性。 | 已覆盖自适应政策与促进多样性；重点是口令创建过程，不是按预算分批整改存量账户。 |
| Yang, Ji, Beyah, 2018, [*DPPG: A Dynamic Password Policy Generation System*](https://nesa.zju.edu.cn/download/DPPG%20A%20Dynamic%20Password%20Policy%20Generation%20System.pdf), IEEE TIFS 13(3):545–558, DOI [10.1109/TIFS.2017.2737971](https://doi.org/10.1109/TIFS.2017.2737971) | 从当前库的分布实时产生差异化政策，目标是让**新用户**口令更分散，并提出多样性指标。 | 不能声称首次以现有分布动态生成政策；本项目的对象是已存在账户和分批通知。 |
| Johnson 等, 2020, [*Skeptic*](https://arxiv.org/html/2007.03809v2), AsiaCCS, DOI [10.1145/3320269.3384762](https://doi.org/10.1145/3320269.3384762) | 对候选组成政策模拟多种用户重选行为，将概率质量重新分配，并按新分布的均匀性比较政策。 | 不能声称首次模拟政策后的口令分布；其目标是政策选择，未在文中构造本项目的账户级逐轮通知预算决策。 |
| Xiao, Zeng, 2022, [*Dynamically Generate Password Policy via Zipf Distribution*](https://ieeexplore.ieee.org/abstract/document/9715109), IEEE TIFS 17:835–848, DOI [10.1109/TIFS.2022.3152357](https://doi.org/10.1109/TIFS.2022.3152357) | HTPG 依据 Zipf 头尾划分及口令特征，为高风险口令生成政策建议。 | 头尾分析和建议策略已有先例；本项目应突出这些建议如何在存量账户预算内被选择与逐轮评估。 |
| Mirian 等, 2023, [*An Empirical Analysis of Enterprise-Wide Mandatory Password Updates*](https://www.acsac.org/2023/program/final/s294.html), ACSAC, DOI [10.1145/3627106.3627198](https://doi.org/10.1145/3627106.3627198) | 研究近 1 万人的强制改密活动，分析通知、用户响应和 IT 支持开销。 | 为“通知不是免费资源”和真实响应率提供依据；本项目实验仍假定通知后全部成功改密，不能以模拟通知人数等同实际运营成本。 |

## 分布研究：为什么要看整个人群

1. Malone 与 Maher, 2012, [*Investigating the Distribution of Password Choices*](https://arxiv.org/abs/1104.3722), WWW, DOI [10.1145/2187836.2187878](https://doi.org/10.1145/2187836.2187878)。较早以多站口令数据检验 Zipf 描述的适用性，适合引出“不能先验假定严格 Zipf”。
2. Bonneau, 2012, [*The Science of Guessing: Analyzing an Anonymized Corpus of 70 Million Passwords*](https://www.ieee-security.org/TC/SP2012/papers/4681a538.pdf), IEEE S&P, DOI [10.1109/SP.2012.49](https://doi.org/10.1109/SP.2012.49)。使用约 7000 万用户的匿名频次信息分析人群差异与猜测风险，适合支撑“口令分布直接关系猜测命中率”。
3. Wang、Cheng 等, 2017, [*Zipf’s Law in Passwords*](https://cssnku.github.io/publications/), IEEE TIFS 12(11):2776–2791, DOI [10.1109/TIFS.2017.2721359](https://doi.org/10.1109/TIFS.2017.2721359)。提出 CDF-Zipf 等拟合方法，是报告讨论排名分布的核心基础文献。
4. Hou 与 Wang, 2023, [*New Observations on Zipf’s Law in Passwords*](https://wangdingg.weebly.com/uploads/2/0/3/6/20366987/tifs22-n2-final.pdf), IEEE TIFS 18:517–532, DOI [10.1109/TIFS.2022.3176185](https://doi.org/10.1109/TIFS.2022.3176185)。在 2.2892 亿条口令上比较模型与拟合优度，发现 stretched-exponential 在其比较中通常比 CDF-Zipf 更准。报告应写“Zipf 类模型描述显著偏斜”，避免“口令严格服从 Zipf”。

## 政策与用户响应：为什么不能只检查规则

1. Blocki、Komanduri、Procaccia、Sheffet, 2013, [*Optimizing Password Composition Policies*](https://arxiv.org/abs/1302.5101), ACM EC, DOI [10.1145/2492002.2482552](https://doi.org/10.1145/2492002.2482552)。理论化政策允许集合、用户偏好与诱导分布，适合支撑“规则强度不等于结果安全”。
2. Becker、Parkin、Sasse, 2018, [*The Rewards and Costs of Stronger Passwords in a University*](https://www.usenix.org/conference/usenixsecurity18/presentation/becker), USENIX Security。考察实际组织中近 20 万次改密和 11.5 万次重置，适合支撑改密的用户成本与政策滞后效应。
3. Sodiya、Owolabi、Onashoga、Oluwumi, 2025, [*A context-based password change enforcement model*](https://www.inderscience.com/info/e_inarticle.php?artid=148110), *International Journal of Information and Computer Security* 27(4):465–487, DOI [10.1504/IJICS.2025.148110](https://doi.org/10.1504/IJICS.2025.148110)。提出综合口令频率、唯一性、尝试、环境和年龄等因素的改密触发模型。属于存量账户动态改密近邻工作；其公开摘要未展示群体分布优化或固定通知预算的逐轮策略，比较时不要臆测全文未做的事。

## 可辩护的研究定位

上述工作已分别研究了分布建模、热门口令拦截、动态创建政策、用户重选后的分布、以及真实改密活动。对本项目更准确的研究问题是：**在固定规模的存量账户中，给定逐轮通知预算和个体口令安全门槛，如何根据每轮更新的完整口令频次分布，联合决定通知哪些账户、使用什么修改规则和通知多少人，并在相同成本下评估分布变化与攻击命中率？**

这是一组已有研究元素的特定组合与可复核实现，不是“首次动态调整口令分布”。截至本次定向检索，未找到与上述全部约束完全相同的论文；这只能作为文献覆盖范围内的观察，不能证明全领域不存在先例。报告可写“现有代表性工作较少同时处理……”，不要写“目前无人实现”。

另须明确：项目当前是模拟整改，假定所有被通知账户都成功修改；因此它验证的是给定响应模型下的优化方法，不是已经在真实组织证明可用的改密流程。完整口令频次更均匀也不自动意味着对所有攻击更安全，必须同时报告固定和重新训练攻击模型的命中率。

## 现有报告中的核查点

- `Overleaf_Report/main.tex` 的背景分析目前引用 Blocki、Segreti、HTPG，但缺少 Schechter、DPPG、Skeptic、Wang、Hou、Mirian 等关键近邻文献。补充时保留真正的 `\cite{...}` 与 `\bibitem{...}` 对应关系。
- 当前 `\bibitem{segreti2017diversify}` 的第二作者写为 `MELARA M S`；[USENIX 出版页](https://www.usenix.org/conference/soups2017/technical-sessions/presentation/segreti)列出的第二作者是 **William Melicher**，应修正。
- 不要将 2025–2026 年的“adaptive password guessing”论文误列为动态防御政策；它们通常研究攻击者如何调整猜测规则，可以作为攻击评估背景，但不是本项目的同类防守方法。
