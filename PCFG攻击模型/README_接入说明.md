# PCFG 攻击模型接入说明

## 来源

- 上游项目：`lakiw/pcfg_cracker`
- GitHub：<https://github.com/lakiw/pcfg_cracker>
- 固定提交：`b04bbdadfe8928fd1287fa73ad1aa46a297ff83a`
- 本地源码：`upstream/pcfg_cracker/`

上游源码保持原样，ZipfGuard 的兼容和安全边界全部实现在
`ZipfGuard/ai/pcfg_adapter.py`。上游各主要 Python 文件包含 MIT 许可声明，
但仓库没有被 GitHub 识别到统一的顶层许可证；正式对外分发前仍应再次核对。

## 安装与版本固定

当前工作区已经包含上游源码，无需联网安装。重新准备环境时，应将上游源码放在
`PCFG攻击模型/upstream/pcfg_cracker/`，并固定到上述提交。可在一个允许联网的
准备环境中执行以下命令，然后把整个目录复制到离线实验机：

```powershell
git clone https://github.com/lakiw/pcfg_cracker PCFG攻击模型/upstream/pcfg_cracker
git -C PCFG攻击模型/upstream/pcfg_cracker checkout b04bbdadfe8928fd1287fa73ad1aa46a297ff83a
```

上游列出 `chardet` 依赖；ZipfGuard 总是显式传入 UTF-8 编码，因此它在当前接入中
是可选依赖。需要与上游完整命令行能力一致时，可在所用 Python 环境中安装：

```powershell
python -m pip install -r PCFG攻击模型/upstream/pcfg_cracker/requirements.txt
```

适配器的可用性检测会核对入口脚本、固定提交和运行解释器，并在报告中记录结果。

## 接入模式

ZipfGuard 使用上游的两个程序，但不在第三方源码目录中直接执行：

- `trainer.py`：只使用合成数据的 `train` 划分训练 PCFG ruleset；
- `pcfg_guesser.py`：按 PCFG 概率顺序生成数量受限的候选。

首次使用时，适配器只把运行所需的 Python 文件复制到版本化的
`runtime/backend/<commit>/` 隔离目录。训练规则、会话状态和缓存全部写入该目录，
第三方源码树保持只读。ZipfGuard 主包只依赖统一的 `BaselineAttacker` / 
`RankingResult` 接口，不导入或修改上游内部模块。

适配器强制使用 `coverage=1.0`，其他值会被配置校验拒绝。此时训练出的文法不包含 OMEN/Markov 基础结构，
确保“PCFG 结构化攻击”不会与项目中的字符 n-gram 或未来的 OMEN 攻击混合。

生成结果只与 ZipfGuard 的公开合成候选空间求交，最终报告只保存排名和聚合指标。
程序不连接认证接口，不执行登录尝试，不使用 test 划分训练或选参。

训练输入只来自 orchestrator 提供的 `train`；PCFG 不使用 validation 选参，test 只在
统一的 `evaluate_ranking()` 中评价。M2 基线、M3 冻结/自适应攻击和 M4 validation
策略搜索均复用项目原有的预算、命中率、覆盖率与 Wilson 区间定义。

## 运行

从 `ZipfGuard` 目录执行：

```powershell
python run_demo.py --pcfg --pcfg-limit 20000 --synthetic-size 5000 --bootstrap 20
```

主要选项：

- `--pcfg`：启用 PCFG；
- `--pcfg-limit`：上游单次最多生成多少候选，必须位于 1 到 1,000,000；
- `--pcfg-timeout`：一次训练或生成的超时秒数。

不传 `--pcfg` 时，原有快速基线不变。传入后，报告的 M2、M3 和 M4 最终评估中会出现
`PCFG 结构化攻击`；M4 的 validation 策略搜索也将它纳入最坏攻击者比较。

固定随机种子的小规模复现命令：

```powershell
python run_demo.py --pcfg --pcfg-limit 2000 --pcfg-timeout 180 --synthetic-size 1000 --bootstrap 20 --seed 42 --json-out reports/pcfg_smoke.json --report-out reports/pcfg_smoke.md
python -m unittest tests.test_pcfg_adapter -v
python -m unittest discover -s tests -v
```

PCFG 上游本身按确定的概率与字典序生成；ZipfGuard 的合成数据、用户响应和策略搜索
使用显式 seed。相同版本、配置、seed 和 Python 环境应得到相同排名与指标。

## 数据边界

- 允许：仓库合成数据、明确用于科研评测的公开基准数据、临时单元测试数据；
- 禁止：真实账户尝试、在线认证、真实凭据验证和第三方服务调用；
- PCFG 只接受具有固定 train/validation/test 划分的用户级实验数据；聚合频次输入
  不含训练划分，命令会拒绝在其上启用 PCFG；
- 候选只在本地测试划分上做集合匹配，不执行登录或网络请求。

## 本地生成物与清理

- `runtime/metadata/`：ruleset 的训练元数据，不含口令正文；
- `runtime/training/`：临时训练文件目录，训练结束后立即删除文件；
- `runtime/backend/<commit>/Rules/ZipfGuard_*/`：隔离后端中的 PCFG ruleset 缓存。

训练失败、超时或输入校验失败时，适配器同样在 `finally` 路径删除临时明文文件；
候选生成会话文件也会及时删除。ruleset 是可复现缓存，不包含原始逐行训练文件，但
会包含从训练数据导出的终结符与统计量，因此整个 `runtime/` 都不应提交或公开。
需要完全清理时，可在确认路径后删除 `PCFG攻击模型/runtime/`；下一次实验会重建。

## 安全边界

- 仅使用合成或明确授权的数据；
- train 用于拟合，validation 只用于其他攻击器选参，test 只用于最终评价；
- 候选数量有硬上限；
- 不导出可直接用于真实账户攻击的候选清单；
- PCFG 分数和排名是模型条件下的相对风险，不是绝对破解次数。

## 已知限制

- PCFG 通过本地子进程运行，速度慢于内置频次和字符 n-gram 基线；
- 候选硬上限会截断概率序列，较小预算下的覆盖率可能偏低；
- PCFG 不使用 validation 调参，因此它与会调参的字符 n-gram 在模型选择复杂度上不同；
- ruleset 缓存按训练序列哈希复用；改变顺序、版本或配置会生成新缓存；
- 当前适配器验证的是固定上游提交，升级上游前需要重新做兼容和回归测试。
