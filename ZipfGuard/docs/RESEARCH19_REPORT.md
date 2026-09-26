# research19 阶段报告

日期 2026-09-26。两个研究假设都没有被冻结测试支持。`claim_supported` 为否。比赛名称、截止日期和提交格式仍是待确认。

明文口令不在这些报告里。小预算命中率不是“新方法已经优于 HTPG”。

## 复跑

小预算入口：

```text
python -m experiments.research19_rerun
```

它会重跑 hak5 的频次、OMEN、PCFG，较小站点的频次词典，hak5→hotmail，配额探索，切分稳定性和 SVG。PassGPT / PassLLM 的 1000 条抽样单独跑：

```text
python -m experiments.research19_neural_eval
```

图由 `reports/research19/frequency_matrix.json` 和 `distribution_diagnostics.json` 画出，文件在 `reports/research19/figures/`。

## hak5 同站

出现次数划分，种子 19，测试 598 条。生成器只看训练部分。

| 攻击器 | 预算 100 | 预算 1000 | 说明 |
| --- | ---: | ---: | --- |
| 训练频次词典 | 68/598 | 134/598 | 不用测试集频次排序 |
| OMEN stdout | 36/598 | 36/598 | `enumNG -p`，不用 `-s` |
| PCFG 原始流 | 26/598 | 74/598 | 没有候选过滤，没有重编号 |
| PassGPT 抽样 | 0/598 | 0/598 | 公开 10 字符模型。测试集里 77 条长于 10，仍留在分母里。长度域内是 0/521 |
| PassLLM 抽样 | 9/598 | incomplete | 原始 1000，唯一 968。预算 968 为 10/598。不是 DivideSearch |

PassGPT 和 PassLLM 的权重都在 RockYou 上训练过。这次 hak5 划分不能把它们说成干净训练。

## 频次词典，预算 1000

完成的站点：myspace 428/8309，phpbb 6184/51084，hotmail 83/1964，faithwriters 172/1943，hak5 134/598，singles 518/3251，twitter 299/7905。其余 12 个站点因为不同字符串超过 20 万，记为 incomplete，没有填 0。

## 预先写定的跨站

hak5 训练，hotmail 测试 1964 条。预算 1000：频次 17，OMEN 18，PCFG 22。分母都是 1964。

## 探索性配额

总预算 900，验证集选中频次词典。测试集单模型 127/598。三个模型各 300 条的并集只有 821 个唯一候选，猜中 93/598。并集没有更高，而且唯一候选没有到 900。这不能写成 H2 成立。

## 拟合与切分

7 个小站有 `frequency > 3` 的 OLS。hak5 的三模型 MLE 盖住了该站全部 2351 个类型，BIC 差小于 10 的是 cdf-Zipf 和 stretched exponential。这是同一样本上的 CDF 误差，不是独立测试。另外 12 站没有做三模型比较。

hak5 一半子样本、20 次：曲率切分成员的平均 Jaccard 约 0.734，累计质量 0.5 约 0.455。这一站没有支持“质量阈值更稳定”。

## 还缺的格子

- 1e5、1e6、1e8。
- 19 站上的 PCFG、OMEN、PassGPT、PassLLM。
- PassLLM DivideSearch。
- FLA、PassGAN：权重在 `local_attack_models/`，Python 3.14 没有 TensorFlow。
- PassFlow：没有权重。
- 修改前/修改后，以及 frozen/adaptive。
- 四个大站的特征提取仍然跳过，不能写成“没有该特征”。

逐项四标志见 `docs/AGENT_EXECUTION_STATUS.md`。
