"""Pinned external research models. No target data is sent to a generator."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / 'local_attack_models'
PASSLLM_ARTIFACT = (ROOT.parent / 'Available artifacts for USENIX Security 2025 #772-v1'
                    / 'Available artifacts for USENIX Security 2025 #772-v1')
PASSLLM_BASE = MODEL_ROOT / 'base' / 'Qwen2.5-0.5B-Instruct'
PASSLLM_ARTIFACT_HASHES = {
    'README_artifact_v1.md': 'f852488b5c296456b63318e33e5666409479cbe17a78d45339ebc14094ce4f07',
    'src/search/generation.py': 'be4cb4d9f1cde9219e476b8d6a4f81e880e6631eedfe1564f4e480a391f4f7be',
    'src/search/search.py': '8fc841b1fe919fdb19a102bcf669fad7de65984c3f03867a8a24708ac9413350',
    'src/model/eval.py': '1e20272ede9e49681727f0ce6bb3133c39cb5dd4cb42cd89675e4e47f6542cf8',
    'checkpoints/rockyou_100w_disQwen0.5B/adapter_model.safetensors':
        '3226fd9a87fab91c9a0e2db0c4ac6c9bfe97ba97622d3bcc5ca6928842053cab',
}
SOURCES = {
    'omen': {'url': 'https://github.com/RUB-SysSec/OMEN',
             'commit': '10aa99e30bb88a10052d389feb53f739254eb1d1'},
    'passgpt': {'url': 'https://github.com/javirandor/passgpt',
                'commit': 'e785194a590228a4e04cc58f2710276929814917'},
    'passllm': {'url': 'https://zenodo.org/records/15612295',
                'status': 'author_artifact_received'},
}
DEFAULTS = {
    'python': str(MODEL_ROOT / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')),
    'wsl_distribution': 'Ubuntu-20.04',
    'training_timeout_seconds': 3600,
    'omen': {'order': 3},
    'passgpt': {'device': 'cpu', 'threads': 4, 'max_length': 32,
                'layers': 8, 'heads': 12, 'embedding': 768,
                'epochs': 3, 'train_batch_size': 16, 'generation_batch_size': 64,
                'learning_rate': 5e-5, 'temperature': 1.0, 'top_p': 1.0, 'top_k': 0},
    'passllm': {'python': str(MODEL_ROOT / 'passllm_venv' /
                           ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')),
                'threads': 4, 'max_length': 30, 'epochs': 1,
                'train_batch_size': 4, 'generation_batch_size': 4,
                'learning_rate': 5e-4, 'lora_r': 16, 'lora_alpha': 64,
                'lora_dropout': 0.2},
}


class ResearchModelUnavailable(RuntimeError):
    pass


def validate_settings(value):
    import copy
    cfg = copy.deepcopy(value)
    if not isinstance(cfg, dict) or set(cfg) != set(DEFAULTS):
        raise ValueError('research_models fields mismatch')
    if not isinstance(cfg['python'], str) or not cfg['python']:
        raise ValueError('research_models.python')
    if not isinstance(cfg['wsl_distribution'], str) or not cfg['wsl_distribution']:
        raise ValueError('research_models.wsl_distribution')
    def integer(v, low, high):
        if type(v) is not int or not low <= v <= high:
            raise ValueError('research model integer setting out of range')
    integer(cfg['training_timeout_seconds'], 1, 86400)
    if not isinstance(cfg['omen'], dict) or set(cfg['omen']) != {'order'}:
        raise ValueError('research_models.omen')
    # Higher orders have an exponential alphabet memory cost in upstream C.
    integer(cfg['omen']['order'], 2, 3)
    p = cfg['passgpt']
    if not isinstance(p, dict) or set(p) != set(DEFAULTS['passgpt']):
        raise ValueError('research_models.passgpt')
    if p['device'] not in ('cpu', 'cuda', 'xpu'):
        raise ValueError('research_models.passgpt.device')
    for key, low, high in [('threads',1,32),('max_length',3,64),('layers',1,24),
                           ('heads',1,16),('embedding',16,1536),('epochs',1,100),
                           ('train_batch_size',1,2048),('generation_batch_size',1,2048),('top_k',0,1000)]:
        integer(p[key], low, high)
    if p['embedding'] % p['heads']:
        raise ValueError('Embedding dimension must divide into attention heads')
    import math
    for key, low, high in [('learning_rate',1e-7,.01),('temperature',.01,10),('top_p',.01,1)]:
        v = p[key]
        if isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(key)
    llm = cfg['passllm']
    if not isinstance(llm, dict) or set(llm) != set(DEFAULTS['passllm']):
        raise ValueError('research_models.passllm')
    if not isinstance(llm['python'], str) or not llm['python']:
        raise ValueError('research_models.passllm.python')
    for key, low, high in [('threads', 1, 32), ('max_length', 3, 30),
                           ('epochs', 1, 100), ('train_batch_size', 1, 128),
                           ('generation_batch_size', 1, 128), ('lora_r', 1, 64),
                           ('lora_alpha', 1, 256)]:
        integer(llm[key], low, high)
    for key, low, high in [('learning_rate', 1e-7, .01), ('lora_dropout', 0, .9)]:
        v = llm[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('research_models.passllm.' + key)
    return cfg


def execute(args, *, cwd=None, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
    return subprocess.run([str(a) for a in args], cwd=cwd, stdin=subprocess.DEVNULL,
                          stdout=stdout, stderr=stderr, timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def source_status(model):
    source = MODEL_ROOT / 'upstream' / model
    if model == 'passllm':
        checks = {name: (file_hash(PASSLLM_ARTIFACT / name) == digest
                         if (PASSLLM_ARTIFACT / name).is_file() else False)
                  for name, digest in PASSLLM_ARTIFACT_HASHES.items()}
        verified = all(checks.values())
        base_files = ('config.json', 'model.safetensors', 'tokenizer.json')
        base_present = all((PASSLLM_BASE / name).is_file() for name in base_files)
        worker_present = (ROOT / 'tools/passllm_worker.py').is_file()
        available = verified and base_present and worker_present
        return {'available': available, **SOURCES[model],
                'source_available': verified, 'artifact_path': str(PASSLLM_ARTIFACT),
                'artifact_checks': checks, 'base_model_path': str(PASSLLM_BASE),
                'base_model_present': base_present,
                'pipeline_integrated': worker_present,
                'checkpoint_scope': 'author RockYou LoRA: interface reproduction only',
                'reason': ('author artifact fingerprint mismatch' if not verified else
                           'Qwen2.5-0.5B-Instruct base model missing' if not base_present else
                           'development-only training adapter missing' if not worker_present else None)}
    try:
        revision = execute(['git', '-C', source, 'rev-parse', 'HEAD']).stdout.decode().strip()
        dirty = execute(['git', '-C', source, 'status', '--porcelain', '--untracked-files=no']).stdout.strip()
        available = revision == SOURCES[model]['commit'] and not dirty
    except (OSError, subprocess.SubprocessError):
        revision, available = None, False
    return {'available': bool(available), **SOURCES[model], 'detected_commit': revision,
            'source_path': str(source)}


def preflight(attackers, cfg):
    """Fail before reading a large corpus if a required source is missing."""
    validate_settings(cfg)
    for model in ('omen', 'passgpt', 'passllm'):
        if attackers.get(model) != 'required':
            continue
        if not source_status(model)['available']:
            raise ResearchModelUnavailable(f'{model}: verified source unavailable; no fallback')
        if model == 'omen' and any(not (MODEL_ROOT/'upstream/omen'/name).is_file()
                                   for name in ('createNG', 'enumNG', 'alphabetCreator')):
            raise ResearchModelUnavailable('OMEN binaries not built')
        if model == 'passgpt':
            executable = Path(cfg['python'])
            if not executable.is_absolute():
                executable = ROOT / executable
            if not executable.is_file():
                raise ResearchModelUnavailable('Isolated model interpreter missing')
        if model == 'passllm':
            executable = Path(cfg['passllm']['python'])
            if not executable.is_absolute():
                executable = ROOT / executable
            if not executable.is_file():
                raise ResearchModelUnavailable('PassLLM isolated interpreter missing')


def wsl_path(path):
    resolved = Path(path).resolve()
    if os.name != 'nt':
        return str(resolved)
    if not resolved.drive or len(resolved.drive) != 2:
        raise ValueError('WSL backend needs a local drive path')
    return '/mnt/' + resolved.drive[0].lower() + resolved.as_posix()[2:]


def omen_command(cfg, binary, work, args, timeout):
    executable = MODEL_ROOT / 'upstream' / 'omen' / binary
    if os.name == 'nt':
        return [str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/wsl.exe'),
                '-d', cfg['wsl_distribution'], '--cd', wsl_path(work), '--',
                'timeout', '--signal=TERM', '--kill-after=5', str(timeout),
                wsl_path(executable), *map(str, args)]
    return [str(executable), *map(str, args)]


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_stream(path):
    with Path(path).open('r', encoding='utf-8', newline='') as handle:
        for line in handle:
            yield line.rstrip('\r\n')


def generate(model, train, tuning, cfg, generation, seed):
    """Materialize a bounded sequence, return file iterator + audited metadata.

    Persistent trained artifacts depend on training/tuning/config/source, never
    target strings. Generation runs are isolated to avoid concurrent writers.
    """
    from core.corpus import counts_hash
    settings = validate_settings(cfg)
    status = source_status(model)
    if not status['available']:
        raise ResearchModelUnavailable(f'{model}: verified upstream source unavailable')
    signature = {'model': model, 'source': SOURCES[model], 'settings': settings[model],
                 'seed': seed, 'training': counts_hash(train), 'tuning': counts_hash(tuning),
                 'adapter': 'research-models-v1'}
    signature['implementation_sha256'] = file_hash(__file__)
    if model == 'omen':
        signature['binary_sha256'] = {name: file_hash(MODEL_ROOT/'upstream/omen'/name)
                                      for name in ('createNG','enumNG','alphabetCreator')}
    if model == 'passgpt':
        signature['worker_sha256'] = file_hash(ROOT / 'tools/passgpt_worker.py')
    if model == 'passllm':
        signature['worker_sha256'] = file_hash(ROOT / 'tools/passllm_worker.py')
        signature['base_model_sha256'] = file_hash(PASSLLM_BASE / 'model.safetensors')
    key = hashlib.sha256(_canonical(signature).encode()).hexdigest()
    cache = MODEL_ROOT / 'runtime' / model / key
    cache.mkdir(parents=True, exist_ok=True)
    # Each writer owns its training location; publish only by its completed manifest.
    import uuid
    work = cache / str(uuid.uuid4())
    work.mkdir()
    train_file = work / 'training.txt'
    supported = {}
    max_length = 19 if model == 'omen' else settings[model]['max_length']
    min_length = settings['omen']['order'] if model == 'omen' else 1
    for word, count in train.items():
        if type(count) is not int or count <= 0:
            raise ValueError('Research models require positive integer occurrence weights')
        if min_length <= len(word) <= max_length and all(33 <= ord(c) <= 126 for c in word):
            supported[word] = count
    if not supported:
        raise ValueError('No training records within declared model support')
    with train_file.open('w', encoding='utf-8', newline='\n') as stream:
        for word in sorted(supported):
            for _ in range(supported[word]):
                stream.write(word + '\n')
    meta = {**signature, 'training_records_total': sum(train.values()),
            'training_records_used': sum(supported.values()),
            'training_records_outside_support': sum(train.values()) - sum(supported.values()),
            'support': {'min_length': min_length, 'max_length': max_length,
                        'characters': 'ASCII U+0021..U+007E; target denominator is unchanged'},
            'training_data_scope': 'caller supplied development only', 'fallback': None}
    output = work / 'guesses.txt'
    training_timeout = settings['training_timeout_seconds']
    if model == 'omen':
        alphabet_size = len({char for word in supported for char in word})
        alphabet_args = ['--pwList', wsl_path(train_file), '--size', alphabet_size, '--output', 'alphabet']
        create_args = ['--iPwdList', wsl_path(train_file), '-n', settings['omen']['order'], '-A', 'alphabet.alphabet']
        with (work / 'upstream.log').open('wb') as log:
            for binary, args in [('alphabetCreator', alphabet_args), ('createNG', create_args)]:
                result = execute(omen_command(settings, binary, work, args, training_timeout),
                                 cwd=work, timeout=training_timeout + 10, stdout=log, stderr=log)
                if result.returncode:
                    raise RuntimeError(f'OMEN {binary} failed with exit {result.returncode}')
            with output.open('wb') as target:
                result = execute(omen_command(settings, 'enumNG', work,
                                 ['-p', '-m', generation['raw_limit']], generation['timeout_seconds']),
                                 cwd=work, timeout=generation['timeout_seconds'] + 10, stdout=target, stderr=log)
        if result.returncode not in (0, 124, 137):
            raise RuntimeError(f'OMEN generation failed with exit {result.returncode}')
        count = sum(1 for _ in output.open('rb'))
        stop = 'timeout' if result.returncode else ('raw_limit' if count >= generation['raw_limit'] else 'exhausted')
        meta.update(upstream_raw_count=count, source_stop=stop, order='OMEN quantized probability levels')
    elif model == 'passgpt':
        trained = cache / 'trained.json'
        checkpoint = None
        if trained.exists():
            saved = json.loads(trained.read_text('utf-8'))
            if saved['signature'] == signature:
                checkpoint = saved['checkpoint']
                if not saved.get('checkpoint_hashes') or any(
                        file_hash(Path(checkpoint) / name) != digest
                        for name, digest in saved['checkpoint_hashes'].items()):
                    raise ResearchModelUnavailable('PassGPT cached checkpoint fingerprint changed')
        request = {'training_path': str(train_file), 'output_path': str(output),
                   'checkpoint': checkpoint, 'work': str(work), 'seed': seed,
                   'config': settings['passgpt'], 'generation': generation}
        request_file = work / 'request.json'
        request_file.write_text(json.dumps(request), encoding='utf-8')
        with (work / 'upstream.log').open('wb') as log:
            try:
                result = execute([settings['python'], ROOT / 'tools/passgpt_worker.py', request_file],
                                 cwd=ROOT, timeout=training_timeout + generation['timeout_seconds'],
                                 stdout=log, stderr=log)
            except subprocess.TimeoutExpired:
                raise ResearchModelUnavailable('PassGPT worker exceeded total time limit') from None
        if result.returncode:
            raise RuntimeError('PassGPT worker failed; see local upstream.log')
        worker = json.loads((work / 'result.json').read_text('utf-8'))
        meta.update(worker)
        checkpoint_hashes = {p.name: file_hash(p) for p in Path(worker['checkpoint']).iterdir() if p.is_file()}
        meta['checkpoint_hashes'] = checkpoint_hashes
        saved = {'signature': signature, 'checkpoint': worker['checkpoint'], 'checkpoint_hashes': checkpoint_hashes}
        temporary = work / 'trained.json'
        temporary.write_text(json.dumps(saved), encoding='utf-8')
        temporary.replace(trained)
    elif model == 'passllm':
        trained = cache / 'trained.json'
        checkpoint = None
        if trained.exists():
            saved = json.loads(trained.read_text('utf-8'))
            if saved['signature'] == signature:
                checkpoint = saved['checkpoint']
                if not saved.get('checkpoint_hashes') or any(
                        not (Path(checkpoint) / name).is_file() or
                        file_hash(Path(checkpoint) / name) != digest
                        for name, digest in saved['checkpoint_hashes'].items()):
                    raise ResearchModelUnavailable('PassLLM development checkpoint fingerprint changed')
        request = {'training_path': str(train_file), 'output_path': str(output),
                   'checkpoint': checkpoint, 'work': str(work), 'seed': seed,
                   'config': settings['passllm'], 'generation': generation}
        request_file = work / 'request.json'
        request_file.write_text(json.dumps(request), encoding='utf-8')
        with (work / 'upstream.log').open('wb') as log:
            try:
                result = execute([settings['passllm']['python'], ROOT / 'tools/passllm_worker.py', request_file],
                                 cwd=ROOT, timeout=training_timeout + generation['timeout_seconds'] + 30,
                                 stdout=log, stderr=log)
            except subprocess.TimeoutExpired:
                raise ResearchModelUnavailable('PassLLM worker exceeded total time limit') from None
        if result.returncode:
            raise RuntimeError('PassLLM worker failed; see local upstream.log')
        worker = json.loads((work / 'result.json').read_text('utf-8'))
        meta.update(worker)
        if not checkpoint:
            saved = {'signature': signature, 'checkpoint': worker['checkpoint'],
                     'checkpoint_hashes': worker['checkpoint_hashes']}
            temporary = work / 'trained.json'
            temporary.write_text(json.dumps(saved), encoding='utf-8')
            temporary.replace(trained)
    meta_file = work / 'manifest.json'
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    # Raw development plaintext need not remain after successful training.
    train_file.unlink()
    meta['_candidate_file'] = str(output)
    return candidate_stream(output), meta
