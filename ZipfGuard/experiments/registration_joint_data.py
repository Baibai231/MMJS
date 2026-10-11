"""One global occurrence-position partition, persisted before any evaluation."""
from __future__ import annotations
import bisect
from collections import Counter
import gzip
import hashlib
import pickle
import random
from pathlib import Path
from core.corpus import parse_record, counts_hash
from policy.registration_joint import stable_seed


def binary_lines(path):
    with Path(path).open('rb') as handle:
        yield from handle


def save_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with gzip.open(temp, 'wb') as f:
        pickle.dump(value, f, protocol=5)
    temp.replace(path)


def load_private(path):
    with gzip.open(path, 'rb') as f:
        return pickle.load(f)


def partition(source, cfg, path, log=print):
    source = Path(source)
    stat = [source.stat().st_size, source.stat().st_mtime_ns]
    identity = {'stat': stat, 'sizes': cfg['role_sizes'], 'seed': cfg['split_seed'], 'ASCII': True}
    if Path(path).exists():
        data = load_private(path)
        if data['identity'] != identity:
            raise ValueError('Source or partition configuration changed; use a new study directory')
        return data
    sizes = cfg['role_sizes']
    digest = hashlib.sha256()
    total = excluded = invalid = rows = 0
    for raw in binary_lines(source):
        rows += 1
        digest.update(raw)
        try:
            word, count = parse_record(raw, 'password_with_count', 'latin-1')
        except ValueError:
            invalid += 1
            continue
        if any(not 32 <= ord(c) <= 126 for c in word):
            excluded += count
        else:
            total += count
        if rows % 2000000 == 0:
            log(f'corpus scan: {rows:,} rows')
    if total < sum(sizes.values()):
        raise ValueError('Insufficient occurrences for globally disjoint roles')
    ids = random.Random(cfg['split_seed']).sample(range(total), sum(sizes.values()))
    allocation, p = {}, 0
    for name, size in sizes.items():
        for identifier in ids[p:p+size]:
            allocation[identifier] = name
        p += size
    ids.sort()
    roles = {name: [] for name in sizes}
    offset = cursor = 0
    digest2 = hashlib.sha256()
    for raw in binary_lines(source):
        digest2.update(raw)
        try:
            word, count = parse_record(raw, 'password_with_count', 'latin-1')
        except ValueError:
            continue
        if any(not 32 <= ord(c) <= 126 for c in word):
            continue
        end = bisect.bisect_left(ids, offset + count, cursor)
        for identifier in ids[cursor:end]:
            roles[allocation[identifier]].append((identifier, word))
        cursor, offset = end, offset + count
    if digest.digest() != digest2.digest() or cursor != len(ids):
        raise RuntimeError('Source changed during partition')
    for name, records in roles.items():
        random.Random(stable_seed(cfg['split_seed'], name)).shuffle(records)
    # Define two near-balanced groups from training only, before test inspection.
    median = sorted(len(w) for _, w in roles['train'])[len(roles['train'])//2]
    data = {'identity': identity, 'roles': roles, 'median_length': median,
            'metadata': {'source_sha256': digest.hexdigest(), 'valid_ASCII_occurrences': total,
                         'excluded_non_ASCII_occurrences': excluded, 'invalid_rows': invalid,
                         'source_rows': rows, 'role_sizes': sizes,
                         'role_hashes': {k: counts_hash(Counter(w for _, w in v)) for k, v in roles.items()},
                         'role_position_hashes': {k: hashlib.sha256(','.join(str(i) for i, _ in sorted(v)).encode()).hexdigest()
                                                  for k, v in roles.items()},
                         'occurrence_ids_disjoint': True, 'subgroups': f'A: length < {median}; B: length >= {median}',
                         'interpretation': 'source-declared occurrence users, not verified individuals'}}
    save_private(path, data)
    return data


def proportions(scenario, batches):
    if scenario == 'raw':
        return None
    if scenario == 'stationary':
        return [.5] * batches
    if scenario == 'abrupt':
        return [.2 if t < batches//2 else .8 for t in range(batches)]
    if scenario == 'gradual':
        return [.2 + .6*t/(batches-1) for t in range(batches)]
    raise ValueError(scenario)


def stream(records, scenario, seed, users, batches, median):
    rng = random.Random(stable_seed('stream', seed, scenario))
    records = list(records)
    rng.shuffle(records)
    m = users//batches
    assert users % batches == 0
    ps = proportions(scenario, batches)
    if ps is None:
        if len(records) < users:
            raise ValueError('Insufficient registration records')
        return [records[t*m:(t+1)*m] for t in range(batches)]
    a = [r for r in records if len(r[1]) < median]
    b = [r for r in records if len(r[1]) >= median]
    output, ia, ib = [], 0, 0
    for pi in ps:
        nb = round(pi*m)
        na = m - nb
        if ia+na > len(a) or ib+nb > len(b):
            raise ValueError(f'Insufficient subgroup occurrences: need {users} without replacement')
        batch = a[ia:ia+na] + b[ib:ib+nb]
        rng.shuffle(batch)
        output.append(batch)
        ia, ib = ia+na, ib+nb
    return output


def developer_stream(records, scenario, seed, batches, median):
    # Weighted simulation of training records, never target strings.
    rng = random.Random(stable_seed('developer-stream', seed, scenario))
    rows = list(records)
    rng.shuffle(rows)
    m = len(rows)//batches
    ps = proportions(scenario, batches)
    if ps is None:
        return [rows[t*m:(t+1)*m] for t in range(batches)]
    a = [r for r in rows if len(r[1]) < median]
    b = [r for r in rows if len(r[1]) >= median]
    return [[(f'train-scenario-{t}-{i}-{r[0]}', r[1]) for i in range(m)
             for r in [rng.choice(b if rng.random() < pi else a)]] for t, pi in enumerate(ps)]
