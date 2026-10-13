# TriGuard：基于 PCFG 的口令策略实验台

TriGuard 用于研究网站口令规则如何影响口令分布、离线猜测风险和用户修改成本。当前主实验把 100,000 名模拟注册用户按顺序分成 10 批，每批可以采用一条网站口令规则；攻击侧只使用 PCFG，并在训练后通过蒙特卡洛预采样估计口令的猜测次数。

本项目提供本地网页实验台、命令行实验、可复核的配置与测试。网页可以查看十批路径的三项 Top10 排名、F/A0/A1 攻击曲线、最终口令分布，并查询单条口令的估计猜测次数与热门排名。

网站管理员前端和用户端浏览器插件的源码位于 `ZipfGuard/apps/`；可下载的两个构建包及完整工具源码包见[双端工具构建包](ZipfGuard/releases/2026-10-11/README.md)。

## 报告写作位置

后续竞赛报告使用 [Overleaf 报告项目](https://cn.overleaf.com/project/6ac9c5e4a50a568cf0b505a3) 作为在线写作位置（2026-10-10 由用户指定）。后续整理报告正文、LaTeX 源码、图表和参考文献时，默认以此项目为交付目标。

本地已有报告草稿和素材位于 `ZipfGuard/docs/`。Overleaf 项目已于 2026-10-10 通过 Git 克隆到 `Overleaf_Report/`，并验证可使用保存的凭据读取远端。该目录不会自动同步；后续在目录中使用 Git 拉取和推送。

在本项目目录的 PowerShell 终端运行 `./connect_overleaf.ps1`，可将 Overleaf 项目克隆到独立的 `Overleaf_Report/` 目录。首次连接时，只在 Git 的密码提示中输入 Overleaf Git 身份验证令牌。克隆成功后，该目录使用自己的 Git 历史；本项目的 Git 仓库会忽略它。

## 第三展示台：局部账户干预

新增 [存量账户局部干预](ZipfGuard/docs/SELECTIVE_INTERVENTION.md)，在固定账户总数下每轮选择对象、局部要求和干预人数。当前完整实验比较 Google 固定、动态重算、随机选人和初始一次规划等方案；单轮最多通知 2%、累计最多通知 20%。模拟中被通知账户全部成功改口令，只有固定 F 攻击曲线面积严格下降的方案才执行并计入通知。

启动同一网页服务后访问 /intervention；图例、猜测预算刻度和分布图复用第二展示台。快速运行入口为 tools/run_intervention_study.py --preset intervention_smoke（在 ZipfGuard 目录中运行）。该实验使用独立协议，不替换下文的十批注册研究。

## 当前研究问题

论文 HTPG 的严格计算核心、接口与核验入口已接入同一服务，访问 `/intervention/htpg`。完整原始口令迭代仍缺论文未公开的类别数值编码、词表和修改函数；页面明确展示缺口，尚未运行的论文组指标留空。全量频次核验及当前方法快照对照见 [HTPG 严格复现记录](ZipfGuard/docs/HTPG_PAPER_REPRODUCTION.md)。

实验从 Top15 网站资料中整理可执行的口令规则，比较完整的十批规则路径：

- **分布：** 最终口令碰撞概率，越低表示用户口令越分散。
- **猜测：** PCFG A1 在 $10^6$ 次估计猜测下的累计命中率，越低越好。
- **用户修改：** 因规则限制而修改原口令的用户比例，越低越好。

选路首先要求每批修改率不超过 60%，然后以 A1 命中率为主目标；命中率相同时，再选择碰撞概率更低的路径。

队友提供的 15 个网站标签形成 $15^{10}=576{,}650{,}390{,}625$ 条形式路径。缺少可执行普通口令规则、违反修改率上限以及规则等价的标签路径需要分别处理。当前数据上，可行标签最终对应 59,049 条不同规则路径；只有训练集和验证集的逐批口令计数完全相同时才合并，得到 2,304 个等效组完成 PCFG A1 评价。

在当前开发数据和固定蒙特卡洛设置下，Google 规则连续使用 10 批排在第一。另一轮 100,000 用户评价中，A1 在 $10^6$ 次猜测下的估计命中率为 15.706%，用户修改率为 49.625%。这条路径与固定 Google 规则完全相同，因此当前结果没有证明动态切换优于固定策略，也不能直接解释为现实攻击下最安全。

## 快速启动

### 1. 获取代码和数据

项目的大型语料文件通过 Git LFS 管理，请先安装 [Git LFS](https://git-lfs.com/)。

```powershell
git lfs install
git clone https://github.com/Baibai231/MMJS.git
cd MMJS
git lfs pull
```

### 2. 创建 Python 环境

项目要求 Python 3.10 或更高版本。以下命令适用于 Windows PowerShell：

```powershell
cd ZipfGuard
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
```

Linux 或 macOS：

```bash
cd ZipfGuard
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-demo.txt
```

### 3. 生成一个快速实验结果

```powershell
.\.venv\Scripts\python.exe tools\run_dynamic_study.py --preset dynamic_smoke
```

快速预设使用 300 名注册用户检查完整流程。它仍需扫描真实带频次语料；首次运行时间取决于磁盘速度，后续运行可以复用本地扫描缓存。

### 4. 启动实验台

```powershell
.\.venv\Scripts\python.exe -u web\server.py --host 127.0.0.1 --port 8765
```

浏览器打开 <http://127.0.0.1:8765/>。

如果需要在同一局域网内展示给其他人，改为：

```powershell
.\.venv\Scripts\python.exe -u web\server.py --host 0.0.0.0 --port 8765
```

然后让对方访问 `http://你的局域网IPv4地址:8765/`。Windows 防火墙询问时，只允许受信任的专用网络。

## 完整实验与路径搜索

运行 100,000 用户的完整注册实验：

```powershell
.\.venv\Scripts\python.exe tools\run_dynamic_study.py --preset dynamic_full
```

运行可行十批规则路径的 PCFG A1 全量评价：

```powershell
.\.venv\Scripts\python.exe tools\exhaustive_site_sequence_a1.py
```

完整实验计算量明显高于快速预设。脚本支持检查点续跑，结果写入 `ZipfGuard/reports/`；该目录默认不进入版本控制，因为其中可能包含体积很大的中间文件和仅供本机查询的索引。

## F、A0 和 A1

| 层次 | 攻击者知识 | 实验含义 |
| --- | --- | --- |
| F | 使用原始开发口令训练 PCFG | 不知道注册规则的冻结攻击模型 |
| A0 | 沿用同一 PCFG，过滤不符合注册规则的猜测 | 知道规则，但不重新训练 |
| A1 | 使用策略实施后的开发口令重新训练 PCFG | 知道规则及其对用户口令的影响 |

攻击图横轴为累计猜测次数，按 10 的次方显示；纵轴为累计猜出的用户口令比例，分母始终为该方案的全部用户。模型未覆盖的用户仍保留在分母中。

## 项目结构

```text
MMJS/
├── ZipfGuard/
│   ├── ai/             # PCFG 与蒙特卡洛排名索引
│   ├── configs/        # 快速、完整及 Top15 实验配置
│   ├── core/           # 攻击、分布和风险计算
│   ├── experiments/    # 实验管线
│   ├── policy/         # 网站规则与用户响应
│   ├── tools/          # 运行、搜索、审计和报告脚本
│   ├── web/            # 本地实验台与图表
│   ├── tests/          # 单元测试和浏览器验收
│   └── docs/           # 协议、数据口径与实验说明
├── PCFG攻击模型/       # PCFG 上游实现及接入说明
└── rockyou-withcount.txt  # Git LFS 管理的带频次语料
```

## 验证

```powershell
cd ZipfGuard
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

浏览器验收还需要 `requirements-ui-test.txt` 中的依赖和本机 Edge。核心实验结果应结合保存的配置、数据哈希、随机种子、模型覆盖率与停止状态解释。

## 数据与结果边界

- 原始语料只用于离线实验，不应通过网页接口公开口令清单。
- 蒙特卡洛排名是估计值；前驱样本不足和模型未覆盖会在报告中单独标记。
- 不同随机种子可以来自同一份源语料，不能自动视为完全独立的数据集。
- Top15 标签中缺少可执行规则的网站不会被虚构成可评分策略。
- 当前结果依赖所用语料、用户响应模型、PCFG 实现、猜测预算和修改成本约束。

更详细的实验口径见 [Top15 十批路径说明](ZipfGuard/docs/TOP15_SEQUENCE_STATUS.md)和 [PCFG 蒙特卡洛实验说明](ZipfGuard/docs/PCFG_MONTE_CARLO_TOP15.md)。

## 当前完整项目归档

[2026-10-13 项目归档](ZipfGuard/docs/project_snapshot_20261013/README.md)包含最新报告源码与图片、方案图源文件以及可公开的实验结果。报告来源版本与文件哈希见归档清单。
