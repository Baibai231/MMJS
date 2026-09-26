"""What the current fitters can and cannot claim on the 19 sites."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def benchmark_status() -> dict:
    return {
        "protocol": "research19-v1",
        "retained_baseline": "htpg-pdf-zipf-ols-v1 with frequency > 3",
        "empirical_frequency_baseline": "available for closed ranking on a pre-registered list, not as a full-corpus probability model",
        "three_model_mle": {
            "ids": ["zipf", "cdf_zipf", "stretched_exponential"],
            "category_cap": 100_000,
            "full_corpus_status": "incomplete",
            "reason": "多数站点的独特字符串超过 100000。截断拟合不能写成全量分布比较。",
        },
        "selection_rule": "BIC 差小于 10 的子集内按预算点 CDF 误差",
        "independent_test_on_19_sites": False,
        "claim_supported": False,
        "plaintext_retained": False,
    }


def main() -> int:
    output = ROOT / "reports" / "research19" / "fit_benchmark_status.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(benchmark_status(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
