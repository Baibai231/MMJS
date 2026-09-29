"""Disjoint occurrence sampling for a simulated registration timeline."""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import random
import uuid
from collections import Counter
from pathlib import Path

from core.corpus import parse_record, counts_hash


def registration_from_counts(counts, *, users, development, seed, cohort_size):
    """A small in-memory equivalent used by examples and tests."""
    expanded = [word for word, count in sorted(counts.items()) for _ in range(count)]
    if users < 1 or development < 3 or users + development > len(expanded):
        raise ValueError('注册与开发样本的总量超过有效出现记录')
    rng = random.Random(seed)
    picked = rng.sample(range(len(expanded)), users + development)
    return _assemble([expanded[i] for i in picked[:users]],
                     [expanded[i] for i in picked[users:]], seed, cohort_size,
                     {'source_format': 'in_memory', 'source_occurrences': len(expanded)})


def load_registration(source, *, source_format='password_with_count',
                      encoding='latin-1', users=100_000, development=100_000,
                      seed=42, cohort_size=10_000, progress=None, cache_dir=None):
    """Sample disjoint occurrences over the full file without expanding it."""
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source_format == 'unique_dictionary':
        raise ValueError('去重字典不能表示用户口令频次')
    stat = source.stat()
    cache_root = (Path(cache_dir) if cache_dir is not None else
                  Path(__file__).resolve().parents[1] / 'reports' / 'dynamic' /
                  'scan_cache')
    cache_key = hashlib.sha256((str(source) + '\0' + source_format + '\0' +
                                encoding).encode('utf-8')).hexdigest()
    cache_path = cache_root / f'{cache_key}.json'
    cached = None
    try:
        payload = json.loads(cache_path.read_text(encoding='utf-8'))
        if (payload.get('source') == str(source)
                and payload.get('source_format') == source_format
                and payload.get('encoding') == encoding
                and payload.get('size') == stat.st_size
                and payload.get('mtime_ns') == stat.st_mtime_ns
                and all(type(payload.get(key)) is int and payload[key] >= 0
                        for key in ('total', 'rows', 'invalid'))
                and isinstance(payload.get('sha256'), str)
                and len(payload['sha256']) == 64):
            cached = payload
    except (FileNotFoundError, ValueError, OSError, TypeError):
        pass
    if cached is None:
        total = rows = invalid = 0
        digest = hashlib.sha256()
        if progress:
            progress('扫描语料第 1/2 遍：核对频次和文件内容')
        with source.open('rb') as stream:
            for raw in stream:
                rows += 1
                digest.update(raw)
                try:
                    _, count = parse_record(raw, source_format, encoding)
                except (ValueError, UnicodeError):
                    invalid += 1
                    continue
                total += count
                if progress and rows % 1_000_000 == 0:
                    progress(f'扫描语料第 1/2 遍：已读取 {rows:,} 行')
        source_sha256 = digest.hexdigest()
    else:
        total, rows, invalid = (cached[key] for key in ('total', 'rows', 'invalid'))
        source_sha256 = cached['sha256']
        if progress:
            progress('复用语料统计缓存；正在核验文件并抽样（第 2/2 遍）')
    if users < 1 or development < 3 or cohort_size < 1 or users + development > total:
        raise ValueError('注册与开发样本的总量超过有效出现记录')
    if progress:
        progress('扫描语料第 2/2 遍：抽取互不重叠的出现位置')
    rng = random.Random(seed)
    chosen = rng.sample(range(total), users + development)
    registration_positions = set(chosen[:users])
    ordered = sorted(chosen)
    registrations, dev = [], []
    cursor = offset = scan_rows = 0
    second_digest = hashlib.sha256()
    with source.open('rb') as stream:
        for raw in stream:
            scan_rows += 1
            second_digest.update(raw)
            try:
                word, count = parse_record(raw, source_format, encoding)
            except (ValueError, UnicodeError):
                continue
            end = bisect.bisect_left(ordered, offset + count, cursor)
            for index in ordered[cursor:end]:
                (registrations if index in registration_positions else dev).append(word)
            cursor, offset = end, offset + count
            if progress and scan_rows % 1_000_000 == 0:
                progress(f'扫描语料第 2/2 遍：已读取 {scan_rows:,} 行')
    if second_digest.hexdigest() != source_sha256 or cursor != len(ordered):
        if cached is not None:
            cache_path.unlink(missing_ok=True)
        raise RuntimeError('两次扫描期间语料内容发生变化，已清除失效缓存')
    if cached is None and source.stat().st_size == stat.st_size and source.stat().st_mtime_ns == stat.st_mtime_ns:
        cache_root.mkdir(parents=True, exist_ok=True)
        payload = {'source': str(source), 'source_format': source_format,
                   'encoding': encoding, 'size': stat.st_size,
                   'mtime_ns': stat.st_mtime_ns, 'total': total, 'rows': rows,
                   'invalid': invalid, 'sha256': source_sha256}
        temporary = cache_root / f'.{cache_key}.{os.getpid()}.{uuid.uuid4().hex}.tmp'
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, cache_path)
    return _assemble(registrations, dev, seed, cohort_size,
                     {'source_name': source.name, 'source_format': source_format,
                      'encoding': encoding, 'source_sha256': source_sha256,
                      'source_rows': rows, 'invalid_rows': invalid,
                      'source_occurrences': total})


def _assemble(registrations, development, seed, cohort_size, metadata):
    rng = random.Random(seed ^ 0xA35E91)
    rng.shuffle(registrations)
    rng.shuffle(development)
    n = len(development)
    train_end = max(1, int(n * .6))
    tuning_end = max(train_end + 1, int(n * .8))
    dev = {'train': Counter(development[:train_end]),
           'tuning': Counter(development[train_end:tuning_end]),
           'validation': Counter(development[tuning_end:])}
    if any(not part for part in dev.values()):
        raise ValueError('开发样本太小，无法形成三个非空角色')
    cohorts = [registrations[i:i+cohort_size]
               for i in range(0, len(registrations), cohort_size)]
    return {'cohorts': cohorts, 'development': dev,
            'metadata': {**metadata, 'registration_occurrences': len(registrations),
                         'development_occurrences': n, 'seed': seed,
                         'cohort_size': cohort_size,
                         'registration_order_sha256': counts_hash(Counter(
                             (f'{i}:{word}' for i, word in enumerate(registrations)))),
                         'development_hashes': {key: counts_hash(value)
                                                for key, value in dev.items()},
                         'frequency_interpretation':
                         '文件出现次数作为模拟用户；真实独立账户身份未被核实'}}
