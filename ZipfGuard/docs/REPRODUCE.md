# 复现

在 `ZipfGuard/` 目录执行。macOS 和 Windows 都使用项目虚拟环境，不依赖 GPU，核心命令不访问网络。网页演示需要本机浏览器。MAYA 下载才需要网络和可选的 `gdown`、`py7zr`。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m experiments.reproduce_htpg_baseline --input ..\rockyou-withcount.txt --output reports\htpg_rockyou_fit.json
.\.venv\Scripts\python.exe -m experiments.robustness_protocol --output reports\robustness_protocol.json
```

macOS 或 Linux 把 `.\.venv\Scripts\python.exe` 换成 `.venv/bin/python`。缺 Streamlit 时，对应测试会跳过并说明原因，不会假装通过。

论文复现命令只写频数和拟合参数。合成对照不读取 RockYou。`reports/` 里的 JSON 是当次结果；提交或演示用的图如果来自这些文件，必须标成缓存，并带上文件哈希。现场还可以重跑上面的单元测试和一条小预算合成命令。

Python 版本以 `.python-version` 和虚拟环境实际版本为准。第三方 PCFG 上游提交记录在 `ai/pcfg_adapter.py` 的说明和 README 里。
