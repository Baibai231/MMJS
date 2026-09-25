# 现场一页卡

1. 进入 `ZipfGuard/`。确认没有把 `local_datasets/` 或原始词表拷进演示目录。
2. `.venv/bin/python -m unittest discover -s tests -q`。Streamlit 缺失只跳过一项。
3. 断网时打开已经生成的 `reports/*.json`，并说明它们是带实验协议的缓存，不是当场计算。
4. 需要当场算时，跑 `python -m experiments.robustness_protocol --seeds 1 --sizes 300`。这一条仍是合成数据。
5. 网页：`.venv/bin/python web/server.py --port 8765`。页面顺序是数据来源、论文复现、新方法、同预算对照。合成和聚合的标签不同。
6. 若模型失败，读页面上的原因和实际参与列表。不要换成另一个模型继续讲成功。

交付包包含代码、配置、合成结果、图和报告。不包含原始语料、GPU 权重和真实口令。第三方许可见 `resources/htpg_reference_v1.json` 的 sources 字段和 `docs/RELATED_WORK.md`。
