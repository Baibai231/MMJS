# 论文复现矩阵

对照本地 14 页 PDF（DOI 10.1109/TIFS.2022.3152357）。默认建议规则仍是 `experimental_strict`。`paper_compatible` 只在显式传入时启用。引用键见 `docs/references.bib` 与 `docs/RESEARCH_REFERENCES.md`。

| 原文 | 位置 | 实现 | 差异 | 验证 |
| --- | --- | --- | --- | --- |
| `f_r = C / r^α`，频数 `> 3` 的对数回归 | III.C，式 (1)，第 4 页 | `core/htpg_fit.py` | 带频次 RockYou 总量 32,603,388，论文表 I 为 32,510,281，差 93,107，未解 | `reports/htpg_rockyou_fit.json`：前 1171 名质量 3,899,108，与论文一致；α 三位小数 0.914 |
| 曲率最大点 `x0`，Head 为排名到 `x0` | 式 (2)(3)，图 2，Algorithm 1 | `floor(x0)` | 图 2 写 Rockyou `x0 = 1171`。本文件连续峰约 1172.16，切分 1172，没有改写成 1171 | 同上 fit 报告 |
| 九个特征与 IGR | III.D，图 6 | `core/htpg_features.py`，`core/htpg_igr.py` | 主权重是不同口令等权。频次加权同时计算，但是敏感性。词表是 VADER 与美国人口普查姓氏替代表。类别距离是众数上的 0/1，不是原文对所有特征都写的绝对差 | 等权完美分割时 IGR 为 1；IV 为 0 时 IGR 为空 |
| 只给 HeadSet 建议 | Algorithm 3，第 8 页 | `policy/htpg_generator.py` | 一致：不在头部则空列表 | `test_suggestions_only_for_head_and_follow_igr` |
| `d_head <= d_tail` 才给建议 | Algorithm 3 第 10 行 | `tie_rule=paper_compatible` | 默认 `experimental_strict` 是 `d_head < d_tail`，等距离不给建议 | `test_paper_tie_rule_includes_equal_distance_and_blocks_shorter_length` |
| Length：尾部平均长度大于当前口令才改 | 表 IV | 论文模式只保留变长；实验模式还可缩短 | 缩短不是表 IV 的动作 | 同上测试 |
| LSD：改到尾部的某个 LSD 结构 | 表 IV | 执行器按目标结构重建并用同一提取器复查 | 原文没有给出具体字符串怎么拼 | `test_directed_edits_meet_their_targets` |
| 日期模拟：末尾追加 1980–2020 的随机年 | IV.A.2 | 常量 `PAPER_DATE_YEAR_MIN/MAX` 只作记录。执行器目前追加固定 `2024` | 这是项目定义的响应，不是原文模拟 | 常量在生成器模块；执行器测试不声称复现该随机年 |
| 每个头部口令随机采用两条策略 | IV.C.1 | 合成协议里预算/成本最多两项，但是验证集攻击得分选择，不是原文的随机两条 | 不能把当前选择器写成 Algorithm 3 | 选择器测试与 `robustness-v4` 诊断 |
| 最好/最差特征由 IGR 排序，不按测试命中挑选 | IV.C.2，图 10 | `_paper_features` 取等权 IGR 前两名 | 还没有在原语料上重画图 10 | 合成对照不是图 10 |
| 迭代：一半头部不变，剩下各四分之一走第一、第二策略；轮次含 1、2、5、10 | IV.D，图 13 | `experiments/htpg_iteration.py` 是合成用户上的近似 | 178 语料缺失，不能标成图 13 | 缺口见语料清单 |
| PCFG 与 OMEN，预算到 `1e8` | IV.A.3，图 7–10 | PCFG 适配器保留原始序号。Markov substitute 明确不是 OMEN | 没有作者 OMEN，没有 `1e8` 跨站生成 | 开放序号回归只覆盖合成流 |
| 可用性 80.23% | 第 V 节 | 未做用户调查 | 模拟采用率不是该数字 | 报告中禁止写入该数作为本项目结果 |

MAYA 上同名 RockYou、000webhost、Yahoo 的拟合见 `docs/CORPUS_INVENTORY.md`。同名不等于表 I 的同一份文件。178、CSDN、RenRen 本地缺失，图 8、图 9、图 13 的原站点实验未做。
