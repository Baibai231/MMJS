"""Weighted local corpora. Sampling spans the entire file, never a top-row slice."""
from __future__ import annotations
import bisect
import hashlib
import random
import re
from collections import Counter
from pathlib import Path
import numpy as np

SPLITS = ('train', 'tuning', 'validation', 'test')


def counts_hash(counts):
    h = hashlib.sha256()
    for word, count in sorted(counts.items()):
        raw = word.encode('utf-8')
        h.update(len(raw).to_bytes(8, 'big')); h.update(raw)
        h.update(repr(count).encode('ascii')); h.update(b'\n')
    return h.hexdigest()


def parse_record(raw, source_format, encoding='latin-1'):
    """Remove only the record terminator; one separator follows the count."""
    raw = raw.removesuffix(b'\n').removesuffix(b'\r')
    if source_format == 'password_with_count':
        # Count may be left padded. Exactly one space/tab separates password.
        match = re.match(rb'^[ \t]*([0-9]+)[ \t](.*)$', raw)
        if not match:
            raise ValueError('invalid count field')
        count, raw = int(match[1]), match[2]
    elif source_format in ('raw_occurrences', 'unique_dictionary'):
        count = 1
    else:
        raise ValueError('unknown source format')
    word = raw.decode(encoding, errors='strict')
    if count <= 0 or not word or any(ord(c) < 32 or ord(c) == 127 for c in word):
        raise ValueError('empty/control-character password or nonpositive count')
    return word, count


def split_counts(counts, seed=42, fractions=(.6, .1, .1, .2)):
    """Allocate each occurrence once; identical strings may cross splits."""
    if len(fractions) != 4 or min(fractions) <= 0 or abs(sum(fractions)-1) > 1e-9:
        raise ValueError('four positive split fractions must sum to 1')
    rng = np.random.default_rng(seed)
    result = {s: Counter() for s in SPLITS}
    for word, count in sorted(counts.items()):
        if type(count) is not int or count <= 0:
            raise ValueError('input counts must be positive integers')
        for split, n in zip(SPLITS, rng.multinomial(count, fractions)):
            if n: result[split][word] = int(n)
    if any(not values for values in result.values()):
        raise ValueError('划分出现空集，请增大样本规模或更换预注册种子')
    return result


def dataset_from_counts(counts, seed=42, metadata=None, fractions=(.6, .1, .1, .2)):
    splits = split_counts(counts, seed, fractions)
    return {'splits': splits, 'metadata': dict(metadata or {}),
            'sample_occurrences': sum(counts.values()), 'sample_unique': len(counts),
            'split_hashes': {s: counts_hash(c) for s, c in splits.items()}}


def load_corpus(source, *, source_format='password_with_count', encoding='latin-1',
                sample_size=2000, seed=42, fractions=(.6,.1,.1,.2), progress=None):
    """Two-pass occurrence sampling without replacement; bounded memory.

    Duplicate rows merge in the sampled corpus. Global uniqueness is not claimed.
    A second digest detects source changes between scan and sampling.
    """
    source = Path(source).resolve()
    if not source.is_file(): raise FileNotFoundError(f'口令语料不存在：{source}')
    if sample_size < 40: raise ValueError('sample_size must be >= 40')
    digest = hashlib.sha256(); total = rows = invalid = 0
    excluded_frequency = unknown_frequency_rows = 0
    with source.open('rb') as handle:
        for raw in handle:
            digest.update(raw); rows += 1
            try:
                _, count = parse_record(raw, source_format, encoding)
                total += count
            except (ValueError, UnicodeError):
                invalid += 1
                if source_format=='password_with_count':
                    field=re.match(rb'^[ \t]*([0-9]+)[ \t]',raw)
                    if field and int(field[1])>0:excluded_frequency+=int(field[1])
                    else:unknown_frequency_rows+=1
                else:excluded_frequency+=1
    if not total: raise ValueError('没有可解析的有效口令出现记录')
    if progress: progress('数据扫描完成，正在从完整文件范围抽样')
    n = min(sample_size, total)
    indices = sorted(random.Random(seed).sample(range(total), n))
    sample = Counter(); cursor = offset = 0; digest2 = hashlib.sha256()
    with source.open('rb') as handle:
        for raw in handle:
            digest2.update(raw)
            try: word, count = parse_record(raw, source_format, encoding)
            except (ValueError, UnicodeError): continue
            end = bisect.bisect_left(indices, offset + count, cursor)
            if end > cursor: sample[word] += end - cursor
            cursor = end; offset += count
    if digest.hexdigest() != digest2.hexdigest() or cursor != n:
        raise RuntimeError('语料在扫描与抽样之间发生变化，请重新运行')
    metadata = {'dataset_id': source.stem, 'source_name': source.name,
                'source_format': source_format, 'encoding': encoding,
                'source_sha256': digest.hexdigest(), 'source_bytes': source.stat().st_size,
                'source_rows': rows, 'invalid_rows': invalid, 'excluded_frequency': None if unknown_frequency_rows else excluded_frequency,
                'known_excluded_frequency': excluded_frequency, 'unknown_frequency_rows': unknown_frequency_rows,
                'source_occurrences': total, 'source_unique': None,
                'frequency_interpretation': ('字典条目权重；不能解释为用户频率' if source_format == 'unique_dictionary'
                                            else '采用文件声明的出现频次；来源真实性与人员独立性未由程序证明'),
                'sampling': 'uniform occurrence sample without replacement over complete file',
                'requested_sample_size': sample_size, 'seed': seed, 'synthetic': False,
                'split_fractions': list(fractions), 'global_duplicates_audited': False,
                'duplicate_rows': 'sampled duplicates merged; global unique count not inferred',
                'scope': '同源出现记录划分；真实初始口令加显式模拟响应'}
    return dataset_from_counts(dict(sample), seed, metadata, fractions)
