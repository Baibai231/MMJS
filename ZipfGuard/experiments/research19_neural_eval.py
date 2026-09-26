"""PassGPT sampling and PassLLM author sampling on the hak5 test split.

Guess text stays in memory. The report stores counts, lengths, and hashes.
PassGPT uses the author sampling script settings. PassLLM uses the artifact
``random_sample`` path, which is not DivideSearch and not a 1e8 guess list.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from core.attack_stream import account_emissions, evaluate_ordered_stream
from core.occurrence_frequency import load_occurrence_counter
from experiments.research19_smoke import BUDGETS, occurrence_split


ROOT = Path(__file__).resolve().parents[1]
PASSGPT = ROOT / "local_attack_models" / "passgpt" / "weights" / "passgpt-10characters"
PASSLLM_ROOT = ROOT / "local_attack_models" / "passllm" / "Available artifacts for USENIX Security 2025 #772-v1"
PASSLLM_BASE = ROOT / "local_attack_models" / "passllm" / "base" / "Qwen2.5-0.5B-Instruct"
PASSLLM_LORA = PASSLLM_ROOT / "checkpoints" / "rockyou_100w_disQwen0.5B"
PAYLOAD = ROOT / "local_datasets/maya/hak5/extracted/datasets/en/forum/hak5.pickle"
PASSGPT_RAW = 1000
PASSLLM_RAW = 1000


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mark_incomplete(scored: dict, unique_count: int) -> list[dict]:
    points = []
    for point in scored["points"]:
        item = dict(point)
        if item["budget"] > unique_count:
            item["cracked"] = None
            item["rate"] = None
            item["incomplete"] = True
            item["reason"] = "唯一候选数没有达到该预算"
        points.append(item)
    return points


def _length_domain(test: list[str], max_chars: int) -> dict:
    longer = sum(len(password) > max_chars for password in test)
    return {
        "max_chars": max_chars,
        "test_rows": len(test),
        "longer_than_max_chars": longer,
        "within_max_chars": len(test) - longer,
    }


def _score(lines: list[str], test: list[str], *, max_chars: int) -> dict:
    counted = account_emissions(
        {
            "text": line,
            "valid": isinstance(line, str) and line != "",
            "in_domain": isinstance(line, str) and 0 < len(line) <= max_chars,
        }
        for line in lines
    )
    budgets = tuple(sorted(set(BUDGETS) | {counted["unique_candidates"]}))
    scored = evaluate_ordered_stream(counted["ordered_unique"], test, budgets)
    within = [password for password in test if len(password) <= max_chars]
    within_scored = evaluate_ordered_stream(counted["ordered_unique"], within, budgets)
    return {
        "raw_emissions": counted["raw_emissions"],
        "valid_candidates": counted["valid_candidates"],
        "unique_candidates": counted["unique_candidates"],
        "duplicate_count": counted["duplicate_count"],
        "outside_domain": counted["outside_domain"],
        "empty": counted["raw_emissions"] - counted["valid_candidates"],
        "max_char_length_observed": max((len(line) for line in lines if line), default=0),
        "points_all_test_rows": _mark_incomplete(scored, counted["unique_candidates"]),
        "points_within_length_domain": _mark_incomplete(within_scored, counted["unique_candidates"]),
        "length_domain": _length_domain(test, max_chars),
    }


def passgpt_sample(limit: int) -> dict:
    import torch
    from transformers import GPT2LMHeadModel, RobertaTokenizerFast

    started = time.perf_counter()
    device = "cpu"
    tokenizer = RobertaTokenizerFast.from_pretrained(
        PASSGPT,
        max_len=12,
        padding="max_length",
        truncation=True,
        do_lower_case=False,
        strip_accents=False,
        mask_token="<mask>",
        unk_token="<unk>",
        pad_token="<pad>",
        truncation_side="right",
    )
    model = GPT2LMHeadModel.from_pretrained(PASSGPT).eval().to(device)
    batch = 8
    if limit % batch != 0:
        raise ValueError("PassGPT 抽样数必须能被批大小整除")
    lines: list[str] = []
    for index in range(limit // batch):
        torch.manual_seed(19 + index)
        with torch.no_grad():
            generated = model.generate(
                torch.tensor([[tokenizer.bos_token_id]], device=device),
                do_sample=True,
                max_length=12,
                pad_token_id=tokenizer.pad_token_id,
                bad_words_ids=[[tokenizer.bos_token_id]],
                num_return_sequences=batch,
                num_beams=1,
                top_p=1.0,
                temperature=1.0,
            )
        decoded = tokenizer.batch_decode(generated[:, 1:].tolist())
        lines.extend(item.split("</s>")[0] for item in decoded)
    return {
        "status": "completed",
        "mode": "author_generate_sampling",
        "device": device,
        "requested_raw_emissions": limit,
        "seconds": round(time.perf_counter() - started, 3),
        "weight_sha256": _file_sha256(PASSGPT / "model.safetensors"),
        "training_exposure": "public checkpoint trained on RockYou passwords of at most 10 characters; not trained on this hak5 split",
        "length_limit": 10,
        "paper_budget_1e8_completed": False,
        "lines": lines,
    }


def passllm_sample(limit: int) -> dict:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    import random

    sys.path.insert(0, str(PASSLLM_ROOT))
    from src.search import search as search_mod
    from src.search.search import random_sample
    from src.utils.tokenize import get_alpha_vocab, process_test_trawling

    def _reorder_dynamic_cache(past_key_values, beam_idx):
        """Transformers 5 returns DynamicCache; the artifact still expects a tuple."""
        if past_key_values is None:
            return None
        if hasattr(past_key_values, "reorder_cache"):
            past_key_values.reorder_cache(beam_idx)
            return past_key_values
        raise TypeError(type(past_key_values).__name__)

    search_mod._reorder_cache = _reorder_dynamic_cache

    started = time.perf_counter()
    random.seed(19)
    torch.manual_seed(19)
    tokenizer = AutoTokenizer.from_pretrained(PASSLLM_BASE)
    tokenizer.pad_token_id = tokenizer.eos_token_id
    base = AutoModelForCausalLM.from_pretrained(PASSLLM_BASE, torch_dtype=torch.float32)
    model = PeftModel.from_pretrained(base, PASSLLM_LORA, is_trainable=False).merge_and_unload()
    model.eval()
    vocab = get_alpha_vocab(tokenizer)
    prompt = process_test_trawling(tokenizer, 1)["input_ids_no_response"]
    sampled = random_sample(
        model,
        tokenizer,
        vocab,
        batch_size=8,
        max_length=12,
        sample_size=limit,
        prompt_ids=prompt,
        output_file=None,
    )
    lines = [text for _score, text in sampled]
    return {
        "status": "completed",
        "mode": "author_random_sample_not_divide_search",
        "cache_compat": "transformers5_dynamic_cache_reorder",
        "device": "cpu",
        "prompt_id": 1,
        "pii_used": False,
        "requested_raw_emissions": limit,
        "seconds": round(time.perf_counter() - started, 3),
        "base_sha256": _file_sha256(PASSLLM_BASE / "model.safetensors"),
        "lora_sha256": _file_sha256(PASSLLM_LORA / "adapter_model.safetensors"),
        "training_exposure": "RockYou 100w distilled LoRA. This is not a clean hak5 training run, and it is not PassLLM-I/II/III.",
        "length_limit": 12,
        "paper_budget_1e8_completed": False,
        "divide_search_completed": False,
        "lines": lines,
    }


def main() -> int:
    meta, counts = load_occurrence_counter(PAYLOAD)
    split = occurrence_split(counts, seed=19)
    report = {
        "protocol": "research19-v1",
        "site": "hak5",
        "split": "occurrence_60_20_20",
        "seed": 19,
        "source_sha256": meta["source_sha256"],
        "test_rows": len(split["test"]),
        "targets_seen_by_generator": False,
        "plaintext_retained": False,
        "claim_supported": False,
    }
    for name, sampler, limit, max_chars in (
        ("passgpt", passgpt_sample, PASSGPT_RAW, 10),
        ("passllm", passllm_sample, PASSLLM_RAW, 12),
    ):
        try:
            generated = sampler(limit)
        except Exception as exc:
            report[name] = {"status": "failed", "error": type(exc).__name__}
            continue
        lines = generated.pop("lines")
        report[name] = {**generated, **_score(lines, split["test"], max_chars=max_chars)}
        del lines
    output = ROOT / "reports" / "research19" / "neural_eval.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        name: {
            "status": report[name].get("status"),
            "seconds": report[name].get("seconds"),
            "unique": report[name].get("unique_candidates"),
            "points": report[name].get("points_all_test_rows"),
        }
        for name in ("passgpt", "passllm")
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
