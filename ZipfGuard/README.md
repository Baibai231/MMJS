# ZipfGuard / DP-HTPG

本地、可复现的合成口令策略实验。Python 是唯一权威实验核心，命令行、Streamlit 和无额外 Web 依赖的网页共享同一流水线与结果展示。历史 JavaScript 原型明确不参与当前实验。

## 启动网页

在项目目录运行，推荐使用已验收的项目虚拟环境 Python 3.13.9：

```powershell
.\.venv\Scripts\python.exe web/server.py --port 8765
```

打开 <http://127.0.0.1:8765/>。选择“快速演示”（1000 用户、20 次 Bootstrap、3 类内置攻击器）或“完整实验”（20000 用户、120 次 Bootstrap、附加可选 PCFG），点击运行。

Streamlit 使用相同实验与图表：

```powershell
.\.venv\Scripts\python.exe -m streamlit run web/app.py
```

两个网页均可调整种子、样本规模、Zipf 指数、预算、攻击器的必选/可选状态、PCFG 上限与超时、用户响应顺序、成本权重、比较策略和完整 JSON 配置。运行失败会清除旧结果；修改参数后须重新运行。

展示内容包括 M2 攻击表与逐点 95% Wilson 区间、覆盖率、PCFG 原始生成数/匹配数/上限、预算曲线、三模型拟合与经验 CDF、残差、对数与线性坐标、冻结/自适应对照和 validation Pareto 散点。

## 配置与命令行复现

统一配置位于 `configs/quick.json` 和 `configs/full.json`。网页可下载本次完整配置，命令行可直接重放：

```powershell
.\.venv\Scripts\python.exe run_demo.py --preset quick
.\.venv\Scripts\python.exe run_demo.py --preset full
.\.venv\Scripts\python.exe run_demo.py --config configs/quick.json --seed 7 --budgets 50,100,500
.\.venv\Scripts\python.exe run_demo.py --preset quick --pcfg --pcfg-limit 2000
```

每份 JSON 包含完整配置及其 SHA-256、数据内容哈希与版本、候选词表、随机种子、实际参与模型及版本、Git 提交与脏状态、源码文件哈希、实际 Python 路径/版本、依赖版本。JSON 的精确文件 SHA-256 写入同名 `.json.sha256` 文件，避免把文件哈希嵌入自身产生循环。网页同时提供结果、校验和及配置下载。

所有生成结果位于 `reports/`，不进入版本控制。当前验收见 [第一大点验收报告](docs/SECTION1_ACCEPTANCE.md)。

## 攻击器与失败处理

频次、合成字典、字符 n-gram、PCFG 以及本地命令适配器统一为 `fit_select_rank(train, validation, candidates) -> RankingResult`。本地命令桥接协议见 [适配器说明](ai/README.md)。

必选攻击器失败会终止并报错。可选模型失败会记录原因，从整次最坏攻击者比较中排除，并重新运行搜索与评价以保证各策略使用相同模型集合；不会静默替换成 n-gram。

PCFG 仅使用合成 train，固定上游提交 `b04bbdadfe8928fd1287fa73ad1aa46a297ff83a`，强制纯 PCFG 模式。流水线和测试各自使用临时 runtime，结束时清理，不再依赖工作区旧的共享 runtime，也不修改第三方源码。

PassLLM 只提供环境能力检测，尚未接入主评估，当前实验未使用。环境可用不等于实际参与；没有“假回退”结果。

## 聚合数据与 RockYou

```powershell
.\.venv\Scripts\python.exe run_demo.py --input demo_data/synthetic_counts.json
.\.venv\Scripts\python.exe run_demo.py --rockyou ..\lab_basic_50_dicts\Rockyou.txt --max-lines 10000 --top-k 50 --source-semantics unknown
```

聚合 JSON 最小示例：

```json
{
  "dataset_id": "example_counts",
  "total_count": 100,
  "items": [{"rank": 1, "count": 60}, {"rank": 2, "count": 40}],
  "metadata": {"source_type": "aggregate counts"}
}
```

聚合模式只运行分布分析，明确说明 M2/M3/M4 未运行及原因。RockYou 页面显示来源类型、是否去重、实际读取行数、保留类别、top-k 和截断质量；来源默认为未确认，重复行只代表文件内重复次数。去重字典或全唯一条目不能用来推断真实用户频率。分析仅描述读取范围内 top-k 条件分布。

## 验证与环境

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
python -m unittest discover -s tests -v
```

2026-09-22：Python 3.13.9 项目环境 43 项全部通过；系统 Python 3.14.6 运行 43 项，42 项通过，1 项 Streamlit 测试因缺少可选依赖跳过。项目环境的 NumPy/Streamlit 版本见 `requirements-demo.txt`，每份报告另记录实际完整环境。

真实浏览器验收脚本 `tools/verify_ui.py` 需要本机 Edge、Playwright 和已启动的 8765 网页；它覆盖预设切换、真实实验、图表、聚合上传和坏文件处理。

## 结论边界

本轮在工程闭环之外增加了论文 HTPG 基线。默认 `run_demo` 仍是 576 项空间，高预算会饱和。PCFG 的开放评价保留原始生成序号。另有一个同预算协议，支持集大于 576，马尔可夫替代按原始生成序号计预算，并标明它不是论文里的 OMEN：

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
