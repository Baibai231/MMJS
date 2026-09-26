# ZipfGuard / DP-HTPG

本地口令策略研究项目。默认流程已迁移为：**真实带频次数据 → 用户响应 → 独立开放猜测 → 逐口令 Min_auto → 风险与用户成本推荐**。命令行、轻量网页与 Streamlit 共用 Python 核心及报告。

2026-09-26 实施状态、验证证据与研究边界见 [项目状态](docs/PROJECT_STATUS.md) 和 [开放协议说明](docs/OPEN_PROTOCOL.md)。论文定义见 [主方案](DP_HTPG_AI_竞赛详细方案.md)。

## 启动演示

在本目录运行：

~~~powershell
.venv/Scripts/python.exe web/server.py --port 8765
~~~

打开 <http://127.0.0.1:8765/>。默认读取本地 ../rockyou-withcount.txt；缺少文件会明确报错。首次运行会扫描完整文件并从全部有效出现记录中抽样，不会截取文件头部或自动换成合成数据。

~~~powershell
.venv/Scripts/python.exe -m streamlit run web/app.py
~~~

两个界面均支持真实语料路径、格式、编码、抽样量、预算、响应情景、模型集合、资源上限、安全需求与完整配置。页面展示推荐证据、验证前沿、单模型/Min_auto 曲线、F/A0/A1、用户成本与完成率、分布特征和复现信息。下载包含 JSON、SHA-256、HTML 与配置；运行失败清除旧结果。

## 命令行与复现

~~~powershell
.venv/Scripts/python.exe run_demo.py --preset open_quick
.venv/Scripts/python.exe run_demo.py --preset open_full
.venv/Scripts/python.exe run_demo.py --config configs/open_quick.json --seed 7
.venv/Scripts/python.exe run_demo.py --corpus ../rockyou-withcount.txt --sample-size 2000 --budgets 10,100 --risk-budget 100 --response R2 --pcfg required --pcfg-limit 5000
~~~

- open_quick：2,000 次真实出现抽样、三个内置攻击器、R0、预算 10/100/1000。
- open_full：20,000 次真实出现抽样、可选 PCFG、R0/R1/R2、预算 100/1000/10000。
- 两者是开发与研究起始预设，参数未经真实用户行为校准，也不保证所有策略在资源限制内完成预算。
- reports/open_demo.json、同名校验文件、Markdown 和 HTML 记录实际结果。结果目录不进入版本控制，也不导出口令清单。

每个报告保存配置、源文件/划分/候选流哈希、策略及模型参数、停止原因、源码哈希和实际运行环境。修改预算时，推荐预算必须仍包含在预算列表中。

## 多种子、敏感性与消融

先生成冻结计划，再用相同命令去掉 --dry-run 执行：

~~~powershell
.venv/Scripts/python.exe tools/run_open_study.py --preset open_full --seeds 11,23,42,67,101 --responses R0,R1,R2 --ablations distribution,none --dry-run
~~~

可用 --risk-budgets 100,1000,10000 逐预算重新选择策略，--out 指定新的结果目录。脚本在接触数据前保存计划；同一种子的响应、预算及消融共用相同划分。无分布引导消融保留同等候选数量和资源上限；汇总显式检查模型集合是否一致。它不自动把重复划分合并成统计显著性结论。

## 数据与攻击协议

支持“频次 + 一个分隔符 + 口令”和每行一次出现。严格解码并保留有效空格；样本中的同字符串合并权重。train/tuning/validation/test 为 60%/10%/10%/20%，每次出现只分配一次。全文件唯一字符串数和来源真实性不会被程序臆测。

频次、训练派生字典变形、长度条件化字符 n-gram、PCFG 各自生成候选，不接收测试目标清单。Min_auto 在每个口令上取最早猜测位置，等价于预算内命中集合的并集；K 是每模型预算。策略过滤及模型内去重后，每个不同候选都计费，包括语料之外的猜测。

必选模型失败终止；可选模型失败后从整次比较排除并重算。资源截断不记为零风险。PCFG 使用隔离临时目录、固定上游提交与纯 PCFG 模式；[接口说明](ai/README.md)。PassLLM/RFGuess 与 A2 尚未接入。

## 聚合分析与历史复现

~~~powershell
.venv/Scripts/python.exe run_demo.py --input demo_data/synthetic_counts.json
.venv/Scripts/python.exe tools/legacy_demo.py --preset quick
~~~

只有排名和次数的聚合 JSON 只能做分布分析，不运行攻击、响应和推荐。去重字典不能作为真实用户频次主数据。

旧闭集流程保留在网页 /legacy、web/legacy_app.py、tools/legacy_demo.py；run_demo.py 的 quick/full 也明确指向历史协议。历史结果不能与 open-minauto-v1 混合比较。

## 验证

~~~powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe tools/verify_open_real.py
.venv/Scripts/python.exe tools/verify_open_ui.py --url http://127.0.0.1:8765
~~~

运行依赖见 requirements-demo.txt。浏览器验收另需 requirements-ui-test.txt 中的 Playwright 与本机 Edge；先启动网页。新增测试覆盖计费、并集、数据隔离、R0 成本、响应失败分母、精确枚举顺序、预算未完成、需求不达标、消融及界面错误处理。

## 结论边界

代码迁移与运行验收完成不等于证明推荐优于常见规则。当前为同源真实初始数据与显式模拟响应；小样本、有限攻击预算及模型覆盖限制都保留在报告中。正式论文仍需冻结规模与需求，执行多种子、消融和响应敏感性实验，按实际效应量与不确定性写结论；没有改善或没有可行策略也属于有效结果。

工程中还保留论文 HTPG 基线与同预算合成对照。历史 576 项演示在高预算会饱和；PCFG 和马尔可夫替代的开放评价保留原始生成序号，并明确后者不是论文里的 OMEN：

```powershell
python -m experiments.robustness_protocol --output reports\robustness_protocol.json
```

这个协议比较无策略、传统字符类别、长度加黑名单、论文 IGR 和新的预算/成本排序。数字是合成用户上的结果。

论文复现入口（只输出统计，不输出口令）：

```powershell
python -m experiments.reproduce_htpg_baseline --input ..\rockyou-withcount.txt --output reports\htpg_rockyou_fit.json
python -m experiments.reproduce_htpg_baseline --input ..\rockyou-withcount.txt --output reports\htpg_rockyou_features.json --with-features
```

曲率切分、九特征 IGR 和逐口令建议是独立基线，不替代上面的合成策略搜索。网站分类代码不在本目录的参赛入口里。

独立于 RockYou 的 MAYA 站点只做聚合拟合，不把口令写进报告。需要可选依赖 `gdown` 和 `py7zr`。语料下载到 `local_datasets/maya/`，该目录不进入版本库。

```powershell
python -m pip install -e ".[maya]"
python -m experiments.external_maya_validation --output reports\maya_external_validation.json
```

默认会下载 MAYA 目录里的全部站点，已完成的聚合结果会按文件哈希复用。MAYA 的 rockyou 会算进同一张表，但标成不是独立于论文基线的站点。频率大于 3 的论文门槛失败时，结果会保留，并额外给出标明不是论文口径的敏感性拟合。类别数超过 `--max-feature-types`（默认 700 万）时只保留频次拟合，不计算 IGR。
