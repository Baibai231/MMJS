"""Fixed-order character Markov enumerator. This is not OMEN.

The paper's OMEN emits guesses from a variable-order Markov model in
probability-bin order. This substitute fits one fixed order on the training
strings and expands a pruned best-first beam. Emission indexes are the raw
stream positions. Strings outside the evaluation set stay in the stream and
are not deleted and renumbered.
"""
from __future__ import annotations

import heapq
import math
from collections import Counter

from core.attackers import END, START, _fit_ngram


SUBSTITUTE_ID = "fixed-order-markov-not-omen"
SUBSTITUTE_VERSION = "markov-substitute-v1"
NOT_OMEN = (
    "这不是论文使用的 OMEN。"
    "OMEN 用变阶马尔可夫和概率分箱枚举；这里是固定阶、束搜索剪枝的字符马尔可夫。"
    "两者的 cracked@B 不能写成同一个攻击协议。"
)


def raw_positions(stream: list[str]) -> tuple[int, ...]:
    """1-based indexes in the generator stream, before any candidate filter."""
    return tuple(range(1, len(stream) + 1))


def enumerate_markov(
    train: list[str],
    *,
    order: int = 2,
    smoothing: float = 0.3,
    limit: int = 64,
    max_length: int = 10,
    beam: int = 80,
) -> list[str]:
    """Return guesses in approximate probability order, including unseen strings."""
    if limit < 1:
        raise ValueError("生成条数必须为正")
    if not train:
        raise ValueError("马尔可夫替代需要非空训练集")
    model = _fit_ngram(train, order, smoothing, public_candidates=train)
    alphabet = tuple(character for character in model.alphabet if character not in {END, "\u0000"})
    if not alphabet:
        raise ValueError("训练集没有可用字符")
    counter = 0
    heap: list[tuple[float, int, int, str, str]] = []
    start = START * (order - 1)
    heapq.heappush(heap, (0.0, counter, 0, "", start))
    emitted: list[str] = []
    seen: set[str] = set()
    expansions = 0
    expansion_cap = max(limit * 50, beam * 20)
    while heap and len(emitted) < limit and expansions < expansion_cap:
        nll, _, kind, text, history = heapq.heappop(heap)
        expansions += 1
        if kind == 1:
            if text and text not in seen:
                seen.add(text)
                emitted.append(text)
            continue
        if text:
            end_probability = model.probability(END, history)
            if end_probability > 0:
                counter += 1
                heapq.heappush(heap, (
                    nll - math.log(end_probability), counter, 1, text, history,
                ))
        if len(text) >= max_length:
            continue
        for character in alphabet:
            probability = model.probability(character, history)
            if probability <= 0:
                continue
            counter += 1
            heapq.heappush(heap, (
                nll - math.log(probability), counter, 0, text + character, history + character,
            ))
        if len(heap) > beam:
            heap = heapq.nsmallest(beam, heap)
            heapq.heapify(heap)
    return emitted


def stream_contains_unseen(train: list[str], stream: list[str]) -> bool:
    observed = set(train)
    return any(guess not in observed for guess in stream)


def train_order_invariant(left: list[str], right: list[str]) -> bool:
    """True when two sequences are the same multiset. Enumeration must ignore order."""
    return Counter(left) == Counter(right)
