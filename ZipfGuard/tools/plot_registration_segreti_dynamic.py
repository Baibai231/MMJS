"""Plot only completed, audited results of the new arm and fixed Segreti."""
from pathlib import Path
import argparse
import hashlib
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    root = parser.parse_args().root.resolve()
    evidence_file = root/"deliverables/comparison_evidence.json"
    evidence = json.loads(evidence_file.read_text("utf-8"))
    assert evidence["status"] == "passed"
    cfg = json.loads((root/"manifest.json").read_text("utf-8"))["config"]
    sources = [
        (root/"runs/11/segreti_dynamic/result.json", "动态调整", "#5C79BC", "o"),
        (Path(cfg["comparison_source"])/"runs/11/segreti_struct_m_nohint/result.json",
         "固定Segreti名单", "#70AF3D", "D"),
    ]
    results = [(json.loads(p.read_text("utf-8")), label, color, marker)
               for p, label, color, marker in sources]
    assert all(r["status"] == "complete" for r, *_ in results)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.sans-serif": ["Microsoft YaHei", "DejaVu Sans"],
                         "axes.unicode_minus": False, "pdf.fonttype": 42,
                         "svg.fonttype": "none", "font.size": 10})
    fig, axes = plt.subplots(2, 2, figsize=(10.8, 7.4))
    fig.subplots_adjust(left=.09, right=.97, top=.89, bottom=.18, hspace=.42, wspace=.34)
    for ax, model, title in zip(axes.flat, ["pcfg", "omen", "union_each_model_B"],
                               ["PCFG", "马尔可夫模型（OMEN）", "两模型联合命中"]):
        for r, label, color, marker in results:
            points = r["actual_attacks"][model]["points"]
            ax.plot([p["budget"] for p in points],
                    [100*p["rate_per_initial_user"] for p in points],
                    color=color, marker=marker, linestyle="-", label=label)
        ax.set(xscale="log", ylim=(0, None), title=title,
               xlabel="每模型实际猜测次数" if model == "union_each_model_B" else "实际猜测次数",
               ylabel="猜中账户比例（%）")
        ax.set_xticks(cfg["budgets"], ["1千", "1万", "10万", "100万"])
        ax.grid(axis="y", alpha=.2)
    ax = axes[1, 1]
    values = [r["cost"]["mean_extra_submissions"] for r, *_ in results]
    ax.barh(range(2), values, color=[c for _, _, c, _ in results], height=.5)
    ax.set_yticks(range(2), [label for _, label, _, _ in results])
    ax.set(xlim=(0, max(values)*1.2), title="注册负担", xlabel="人均额外提交次数")
    ax.invert_yaxis()
    for i, value in enumerate(values):
        ax.text(value+.03, i, f"{value:.4f}", va="center")
    for ax in axes.flat:
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("同一份213种Segreti结构名单起点：10万模拟用户完整运行", fontsize=14)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(.5, .07), ncol=2, frameon=False)
    fig.text(.5, .015, "联合曲线按每模型次数标注，最多合计两倍；修改次数不设上限。仅一次同样本探索性比较。",
             ha="center", fontsize=9)
    dest = root/"deliverables"
    for suffix in ["pdf", "png", "svg"]:
        path = dest/f"registration_segreti_dynamic_comparison.{suffix}"
        fig.savefig(path, dpi=220, facecolor="white")
        evidence["outputs"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    plt.close(fig)
    evidence["plot_code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    evidence["visual_check"] = "pending"
    evidence_file.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), "utf-8")
    print("Audited comparison plot exported")


if __name__ == "__main__":
    main()
