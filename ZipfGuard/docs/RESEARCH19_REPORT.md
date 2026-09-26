# research19 阶段报告

日期 2026-09-26。两个研究假设都没有被冻结测试支持。`claim_supported` 为否。比赛名称、截止日期和提交格式仍是待确认。

明文口令不在这些报告里。小预算命中率不是“新方法已经优于 HTPG”。

下面表格里的 OMEN 和 PCFG 次数，来自重复数为 0 的那次运行，所以原始位置和唯一位置当时重合。评价器现在把这两根轴分开保存。PassLLM 请求保留 1000 条时，作者采样会先安排 1104 条轨迹，再排序；那不是和频次词典相同的 1000 次原始生成。

## 复跑

只根据已有 JSON 重建分布摘要、主表和图：

```text
python -m experiments.research19_rerun --flow report
```

缺少 `preprocess_identity.json` 或其他专项结果时会失败，不会把缺文件写成 0。完整小预算生成另跑 `--flow full`。PassGPT / PassLLM 不在这两条流程里：

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
| PassLLM 抽样 | 唯一位置 9/598 | 唯一位置 incomplete | 计划轨迹 1104。保留 1000 条后排序，不是原始生成位置。唯一候选 968，该预算 10/598。排序后的第 100 条是 8/598。不是 DivideSearch |

PassGPT 和 PassLLM 的权重都在 RockYou 上训练过。这次 hak5 划分不能把它们说成干净训练。

## 频次词典，预算 1000

完成的站点：myspace 428/8309，phpbb 6184/51084，hotmail 83/1964，faithwriters 172/1943，hak5 134/598，singles 518/3251，twitter 307/7898。twitter 在当前预处理下是 39487 条、35106 类，排除 31 条内部制表符；旧数据卡的 39518/35137 只作为历史快照。其余 12 个大站这次没有重读，不能推断它们没有内部控制字符。频次矩阵里超过 20 万不同字符串的站点仍然是 incomplete，没有填 0。

## 预先写定的跨站

hak5 训练，hotmail 测试 1964 条。预算 1000：频次 17，OMEN 18，PCFG 22。分母都是 1964。

## 探索性配额

总生成预算 900 已经花完：三个模型各发出 300 条，完成状态是 reached_budget。其中跨模型重复 79 条，唯一验证对象 821。唯一验证预算 900 因此是 incomplete，不能写成资源截断。这 821 条上猜中 93/598。验证集选出的单模型在测试集是 127/598。这不是 H2 的确认结论。

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
