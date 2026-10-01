# 攻击模型接入记录

日期：2026-09-30。本文区分真实执行、接口准备和仍未完成的模型，不把小模型验证当作正式安全结论。

## 上游来源与版本

| 模型 | 来源 | 固定版本与当前状态 |
| --- | --- | --- |
| OMEN | [RUB-SysSec/OMEN](https://github.com/RUB-SysSec/OMEN) | `10aa99e30bb88a10052d389feb53f739254eb1d1`；原版 C 已在现有 Ubuntu-20.04 WSL 编译，训练与生成已实际运行 |
| PassGPT | [javirandor/passgpt](https://github.com/javirandor/passgpt) | `e785194a590228a4e04cc58f2710276929814917`；作者分词器、训练 collator 与 GPT-2 架构已接入，小架构已实际训练生成 |
| PCFG | [lakiw/pcfg_cracker](https://github.com/lakiw/pcfg_cracker) | `b04bbdadfe8928fd1287fa73ad1aa46a297ff83a`；沿用项目接口，`coverage=1.0` 的纯 PCFG |
| PassLLM | [作者发布产物](https://zenodo.org/records/15612295)、[论文](https://www.usenix.org/conference/usenixsecurity25/presentation/zou-yunkai) | 作者 ZIP 已由用户放入工作区；通用猜测代码和 RockYou 蒸馏 LoRA 已核验。基础 Qwen 权重及项目开发数据自训流程仍需验证，尚未参与主评估 |

[Tzohar/PassLLM](https://github.com/Tzohar/PassLLM) 明确为非官方定向攻击复现，本项目不将其冒充作者版通用猜测。PassLLM 名称已可被配置识别，但运行会明确阻止；这只是待接入状态，不是模型已接通。

OMEN 是 MIT 许可。PassGPT 官方声明代码和模型为 CC BY-NC 4.0、仅研究用途；复用作者模块而不改名掩盖来源。PassLLM 作者包未见顶层 `LICENSE`，附带 LoRA 目录的许可字段为空；许可证状态记录为未明确提供，不自行推断。

### PassLLM 作者包核验

实际代码位于项目上一级的 `Available artifacts for USENIX Security 2025 #772-v1/Available artifacts for USENIX Security 2025 #772-v1/`。`README_artifact_v1.md` 说明微调、Monte Carlo、通用 Two-Stage / Divide Search、定向 Dynamic Beam Search 和蒸馏代码。`config/gendic_config.ini` 使用 prompt ID 1，即不需要姓名、生日或旧口令的通用猜测。`model/` 原为空，作者要求另取 `Qwen2.5-0.5B-Instruct` 或 `Mistral-7B-v0.1`。已从 [Qwen 官方模型仓库](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) 取得前者，`model.safetensors` 的 SHA-256 为 `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe`，放在项目忽略目录 `local_attack_models/base/Qwen2.5-0.5B-Instruct`。

以下 SHA-256 已写入来源检查，原始下载目录不会移动或覆盖：

| 文件 | SHA-256 |
| --- | --- |
| `README_artifact_v1.md` | `f852488b5c296456b63318e33e5666409479cbe17a78d45339ebc14094ce4f07` |
| `src/search/generation.py` | `be4cb4d9f1cde9219e476b8d6a4f81e880e6631eedfe1564f4e480a391f4f7be` |
| `src/search/search.py` | `8fc841b1fe919fdb19a102bcf669fad7de65984c3f03867a8a24708ac9413350` |
| `src/model/eval.py` | `1e20272ede9e49681727f0ce6bb3133c39cb5dd4cb42cd89675e4e47f6542cf8` |
| `checkpoints/rockyou_100w_disQwen0.5B/adapter_model.safetensors` | `3226fd9a87fab91c9a0e2db0c4ac6c9bfe97ba97622d3bcc5ca6928842053cab` |

该 LoRA 训练于 RockYou。当前研究评价数据也来自 RockYou，所以它只可用于加载、生成和格式的接口复现。正式攻击评价须从本项目独立的开发训练数据微调，评价用户口令不得进入训练或调参。作者脚本的配置解析使用 `eval()`；本项目不直接执行其 `main.py` 或外来配置。

## 本机环境

- Intel Arc 140T 集成显卡，系统约 64 GiB 内存；系统上报的共享显存名称不视为可独占 GPU 显存保证。
- 已复用 Ubuntu-20.04 WSL 中的 GCC 和 make 编译 OMEN，没有安装或变更操作系统组件。
- PassGPT 神经环境位于 `local_attack_models/venv`，Python 3.11，依赖见 `requirements-research-models.txt`。PassLLM 作者搜索代码需要旧版缓存 API，另建 `local_attack_models/passllm_venv`，使用 `requirements-passllm.txt` 中的 PyTorch 2.6.0、Transformers 4.47.0、PEFT 0.14.0。网页原 Python 3.13 环境未安装深度学习依赖。
- 当前安装实际报告 PyTorch 2.6.0+cpu，CUDA 和 XPU 均不可用。因此目前只验证 CPU。完整 PassGPT 训练及百万预算吞吐尚未测定；不能给出 GPU 加速或正式完成时间承诺。

## 接入方式与公平性

`AttackEngine` 增加 OMEN、PassGPT、PassLLM 入口。模型仅接收调用者提供的开发训练和调参数据，不接收评价目标。候选序列统一进入 `consume`，沿用唯一且满足已知策略的猜测预算。

- F：原开发数据训练，冻结候选序列。
- A0：复用原序列，按已知策略过滤；同一训练配置在不同过滤条件下不用重新采样。
- A1：调用既有开发响应流程，在按策略修改后的独立开发口令上训练。PassGPT 每个新训练语料从随机初始化开始，不导入外部 RockYou 口令权重。
- PassGPT 当前是固定超参数配置，不使用目标或调参命中率挑选超参数；调参指纹仍记录，后续搜索须单独预先约定。
- 原始生成上限和时间上限不能假装为模型自然穷尽；重复、无结束符及无效输出可能导致预算未完成。
- 模型缺失报错，不静默替换。正式配置将四个模型全部设为必选，PassLLM未就绪时在加载大语料前阻止运行。
- 本次包装器直接复用 PassGPT 作者的 tokenizer 和 collator，修正上游生成器硬编码 `.cuda()`、一次保留全部生成结果和静默训练截断问题；它是项目适配版，不声称逐字复现论文的全部实验参数。
- PassLLM 正式入口对项目开发数据从基础 Qwen 重新训练 LoRA，复用作者的通用提示词、字符词表、训练预处理与逐批随机采样核心。原始采样按生成顺序保存；没有结束符的采样计入原始尝试，不冒充已输出的猜测。作者 RockYou LoRA 仅用于独立接口复现，正式入口不加载它。
- 当前 PassLLM 路径是**随机采样基线**，不是作者 Two-Stage / Divide Search。后者会按概率搜索并有不同的预算语义，尚未完成资源有界的接入；不能用当前采样结果声称复现论文最强通用攻击。

## 模型支持范围

当前 OMEN 使用原版二或三阶模型，配置默认三阶；原版实现的长度上限为 19。当前适配器训练范围为可打印 ASCII（不含空格），长度至少为所选阶数。PassGPT 设置最大长度 32，PassLLM 设置最大长度 30，同样显式记录字符范围，不使用受限的公开检查点评价长口令策略。

超出范围的训练记录计数并记录，不截断；超出范围的评价用户保留在原分母中，并由评价器报告 `support_coverage`。这不代表这些用户安全，正式论文需要展示模型覆盖局限。扩展字符和长度范围须另行验证，不修改评价用户来迎合模型。

神经模型的采样顺序不是严格概率排名。当前首次命中位置是固定随机种子下的生成序列位置，不是精确猜测复杂度。

## 配置及运行

- `configs/dynamic_research_smoke.json`：300 名用户、十批，每批 30 人；开发数据 1,000 条；每模型 100 次有效猜测预算、20,000 次原始输出上限。保留已核验的 OMEN、PCFG 和小架构 PassGPT 三模型回归配置。PassLLM 的四模型完整流程仍需另行实测。
- `configs/dynamic_research_full.json`：正式候选配置，OMEN、PCFG、PassGPT、PassLLM 全部必选。PassGPT 使用 8 层、12 头、768 维和 3 个训练 epoch；PassLLM 使用 Qwen 0.5B、项目开发数据 LoRA 训练 3 个 epoch。它们是项目研究配置，非论文结果保证。当前 CPU 吞吐不足以承诺完成全量配置。
- 原 `dynamic_full` 保留为旧模型实验配置，尚未将网页默认切换到缺失模型的配置。新配置已去掉自写 n-gram 和六种字典变换；频率基线保留旧接口，可单独运行，不混入新的四模型主目标。

```powershell
.venv/Scripts/python.exe tools/setup_research_models.py
.venv/Scripts/python.exe tools/run_dynamic_study.py --config configs/dynamic_research_smoke.json --output-dir reports/dynamic/research_models_smoke_300_new
```

首次配置新机器可使用 `tools/setup_research_models.py --install`，它只创建项目内源码及 Python 环境，要求机器已有 WSL/GCC/make 和 uv。源码不一致时保留现场并停止，不重置用户目录。

## 仍需完成

1. PassLLM 作者包、基础 Qwen 和项目开发数据微调采样已接通；下一步决定是否接入作者 Divide Search，并校准更高预算下的吞吐与攻击排序。
2. 当前三模型 300 人验证、PassLLM 独立接口验证和四模型 20 人联跑均已完成；四模型 300 人或更大验证尚未完成。
3. 测量正式 PassGPT 架构的训练和百万预算成本，评估本机 CPU、可用 Intel 加速或另有 GPU 环境的可行性。
4. 在新模型协议冻结后补齐全部 960 条攻击评价，重新比较候选数量；不复用旧攻击命中率冒充新结果。
5. 新四模型攻击目标不可使用依赖旧频率／字典变换的剪枝下界；重新证明前使用保守零下界，见 `EXACT_TIMELINE_SEARCH.md`。

版本、生成参数、源码指纹、训练数据指纹及 PassGPT 检查点文件指纹随每次结果记录。大体积权重、第三方源码和本地口令文件均位于已有忽略目录中，不推送到 Git。

## 实际执行结果与纠错

1. 第一次 300 人运行耗时 87.301 秒。F 和 A1 完成每模型 100 次预算，但 A0 未完成：旧逻辑只过滤了冻结攻击已经保留的前 100 条，无法得到 100 条合规候选。最初进度消息未区分这一点，核验后立即纠正，不将此结果作为完整通过。
2. 新研究模型流程改为继续读取同一冻结的原始序列，再按各批策略过滤，达到预算或报告真实资源上限。原旧模型实验保留原协议。增加了“首个猜测不合规、后续猜测命中”的针对性测试。
3. 第二次运行输出到 `reports/dynamic/research_models_smoke_300_v2`，耗时 **121.729 秒**。300/300 用户保留、0 待处理；无策略、动态策略、固定预设策略的 F/A0/A1 全部完成每模型 100 次预算，所有评价分母均为 300。实际参与模型为 OMEN、PCFG、缩小架构 PassGPT，无模型替代或可选失败。
4. 在该样本的动态最终口令中，3 人超出 OMEN 声明范围，2 人超出 PassGPT 声明范围；均保留分母并公开计数，不能解释为已证明安全。
5. 另用 50 条合成训练记录验证 8 层、12 头、768 维 PassGPT 的 CPU 路径，总耗时约 **15.962 秒**。256 次采样只有 10 条唯一猜测，正确报告原始采样上限导致未完成 100 次有效预算。该数据高度集中，只是训练、重复计数与失败语义验证，不能外推正式数据百万预算速度或安全性能。

两次验证均非 960 条正式筛选实验。第二次报告会显示小模型验证提示；原实验和新实验的源码指纹各自保留，不覆盖历史数据。

## PassLLM 独立验证与成本

1. 使用作者 RockYou 蒸馏 LoRA 和新取的基础 Qwen，按通用提示词 ID 1 实际生成 8 条候选，耗时 **6.594 秒**。该验证只证明权重与作者随机采样路径能加载和输出；8 条未进入 RockYou 政策评价。
2. 使用 50 条合成开发训练记录，从基础 Qwen 独立训练 LoRA 1 个 epoch，共 13 步；再做 32 次原始采样。完整接口耗时 **23.882 秒**，其中训练 12.094 秒、生成 5.656 秒。32 次采样形成 24 条进入评估的原始候选，去重后达到 10 次有效猜测预算。无作者 RockYou 权重参与。
3. 本机这次生成约每秒 5.7 次原始采样。只按这一小样本线性外推，100 万次原始采样约需 **49 小时**，还不含 10 批、多个方案、F/A0/A1 和训练。该数字是容量预警，不是正式百万预算测速。现阶段不能把 960 条策略的四模型全量攻击评价标为已完成。
4. 复现结果与生成文件保存在忽略目录 `local_attack_models/runtime/passllm_author_smoke/`；开发训练检查点和指纹在 `local_attack_models/runtime/passllm/`。两处都不进入 Git。
5. 以 20 名评价用户、50 条开发记录、一批注册、每模型 10 次有效猜测预算、32 次原始输出上限，完成 OMEN、纯 PCFG、PassGPT、PassLLM 的动态流程联跑，报告在 `reports/dynamic/research_models_four_20/report.json`。20/20 用户保留；F 与 A1 的四模型预算均完成。A0 的 OMEN 在首 32 条原始猜测中全被长度≥8 策略过滤，因此该点标为**未完成**，风险区间为 0～1，没有将其写成 0 命中率。这是很小的接口验证，无法比较策略优劣。
