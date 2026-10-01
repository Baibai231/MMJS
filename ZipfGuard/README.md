# ZipfGuard / DP-HTPG

> 当前主实验已切换为 **PCFG + 前置蒙特卡洛 + 队友 Top15 网站资料**。本地首页提供单条口令的猜测次数估计与热门排名查询，保留 F/A0/A1。当前可编码六个强制规则子集（Instagram 为后补证、尚未参加旧全量报告）；建议、缺失和不适用条目分开标注。十批路径的全局搜索尚未完成，不再使用 960 模板池。详见 [新实验说明](docs/PCFG_MONTE_CARLO_TOP15.md)。下文多模型与候选池内容属于历史研究路线。


本地口令策略研究项目。默认流程已迁移为：**真实带频次数据 → 用户响应 → 独立开放猜测 → 逐口令 Min_auto → 风险与用户成本推荐**。命令行、轻量网页与 Streamlit 共用 Python 核心及报告。

2026-09-26 实施状态、验证证据与研究边界见 [项目状态](docs/PROJECT_STATUS.md) 和 [开放协议说明](docs/OPEN_PROTOCOL.md)。论文定义见 [主方案](DP_HTPG_AI_竞赛详细方案.md)。

## 分批注册动态策略实验

新实验按模拟注册顺序执行规则，每一批完成后读取历史累计口令分布，选择下一批是否增加一项兼容规则。页面入口是 <http://127.0.0.1:8765/dynamic>；Streamlit 侧边栏选择“分批注册动态策略”。已实现的模块、评价口径与当前验证状态见[动态实验实施状态](docs/DYNAMIC_IMPLEMENTATION_STATUS.md)，完整设计见[改造计划](docs/分布驱动_分批注册动态策略改造计划.md)。

当前动态实验使用 `visible-first-hidden-retry-v2`：长度和字符类别等显式要求在候选提交前一次满足；黑名单和启用的模式限制在提交后检查，阻断后继续换候选。不再模拟放弃。连续 8 次新候选未通过只触发报告，继续尝试；达到 1,000 次计算上限仍未通过时保留为待处理。最终分布图使用同一批用户的完整频数—排名双对数曲线。历史报告保留旧协议标记，必须重新运行才能获得新逻辑的结果。

~~~powershell
.venv/Scripts/python.exe tools/run_dynamic_study.py --preset dynamic_smoke
.venv/Scripts/python.exe tools/run_dynamic_study.py --preset dynamic_full
.venv/Scripts/python.exe tools/run_dynamic_study.py --config configs/dynamic_primary_full.json --seeds 11,23,42,67,101 --output-dir reports/dynamic/multiseed_primary
.venv/Scripts/python.exe tools/summarize_dynamic_study.py --input-dir reports/dynamic/multiseed_primary
.venv/Scripts/python.exe tools/run_dynamic_study.py --preset dynamic_full --seeds 11,23,67,101 --output-dir reports/dynamic/multiseed_controls
.venv/Scripts/python.exe tools/summarize_dynamic_controls.py --input-dir reports/dynamic/multiseed_controls --seed42-report reports/dynamic/million_paired_seed42/report.json --output-dir reports/dynamic/multiseed_controls
.venv/Scripts/python.exe tools/run_dynamic_sensitivity.py --config configs/dynamic_sensitivity_5k.json --output-dir reports/dynamic/sensitivity_5k
~~~

完整预设抽取互不重叠的 100,000 名模拟注册用户和 100,000 条开发参考出现记录，分 10 批，最高预算为每模型 1,000,000 次有效猜测。每次运行在忽略版本控制的 `reports/dynamic/` 下输出公开 JSON、HTML、SVG 和仅保存在本机的逐用户明文对照文件。百万预算是否实际完成取决于各攻击器的停止状态；未完成点显示上下界。

快速预设虽然只抽样 300 人，仍从完整语料中均匀抽样。首次运行须扫描源文件两遍；程序显示每百万行进度，并把经 SHA-256 核验的语料总频次与行数缓存到本机 `reports/dynamic/scan_cache/`。源文件未变化时，后续运行复用统计并只扫描一遍；扫描时仍逐字节核验文件哈希。当前机器上的实测为首次 48–133 秒、缓存命中约 26–59 秒，耗时随磁盘与系统负载变化。

旧协议的五种子主实验与四类固定策略的百万预算对照已完成，历史汇总页分别保存在 `reports/dynamic/multiseed_primary/cross_seed_report.html` 和 `reports/dynamic/multiseed_controls/cross_seed_controls_report.html`。这些数字包含旧版放弃与重试终止设定，不代表当前协议。每条历史轨迹的完成率、修改率、分布、猜测次数和攻击命中率及其适用范围见[动态实验实施状态](docs/DYNAMIC_IMPLEMENTATION_STATUS.md)。汇总脚本会拒绝人数不守恒、模型缺失或字符模型未跑满百万有效猜测的结果。旧协议五个种子中，动态方案比开发集预选固定规则的分布更分散且修改率更低，但自适应攻击命中率更高，不能宣称它同时胜出。

另有旧协议同样本的 5,000 人顺序与响应压力测试，历史报告位于 `reports/dynamic/sensitivity_5k/sensitivity_report.html`。它表明偏向从常见口令池重新选择时，分布改善并不保证攻击命中率下降；该较小规模实验不能代替主实验。当前敏感性脚本已移除高放弃率情景。

当前对照只保留“无策略、固定预设策略、动态策略”。固定预设全程要求长度至少 8；动态首批使用同一规则，之后按历史分布调整。分布、碰撞概率和成本图使用这三个方案；预算攻击图展示每组的 F、A0、A1，共九条曲线。无策略的三个攻击层次因训练与过滤条件相同而重合。旧的四类固定对照属于历史协议，详见[动态实验状态](docs/DYNAMIC_IMPLEMENTATION_STATUS.md)。[候选池说明与完整组合](docs/PASSWORD_POLICY_CANDIDATES.md)只作讨论，未扩展运行时策略池。

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
