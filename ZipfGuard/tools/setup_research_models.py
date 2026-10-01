"""Provision pinned upstreams and project-local neural environments.

No global packages or OS components are installed. The PassLLM author artifact
and Qwen base model are separately verified by source_status.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai.research_models import SOURCES, MODEL_ROOT, source_status, wsl_path


def run(args, cwd=ROOT):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--wsl-distribution', default='Ubuntu-20.04')
    args = parser.parse_args()
    if args.install:
        (MODEL_ROOT/'upstream').mkdir(parents=True, exist_ok=True)
        for model in ('omen','passgpt'):
            path = MODEL_ROOT/'upstream'/model
            if not path.exists():
                run(['git','clone','--no-checkout',SOURCES[model]['url'],path])
                run(['git','-C',path,'checkout','--detach',SOURCES[model]['commit']])
            if not source_status(model)['available']:
                raise RuntimeError(f'{model}: existing source differs from pinned version; preserved for inspection')
        if os.name == 'nt':
            run(['wsl.exe','-d',args.wsl_distribution,'--cd',wsl_path(MODEL_ROOT/'upstream/omen'),'--','make'])
        else:
            run(['make'], MODEL_ROOT/'upstream/omen')
        uv = shutil.which('uv')
        if not uv:
            raise RuntimeError('uv is required; no global installer is run')
        environment = MODEL_ROOT/'venv'
        if not environment.exists():
            run([uv,'venv','--python','3.11',environment])
        python = environment/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run([uv,'pip','install','--python',python,'-r',ROOT/'requirements-research-models.txt'])
        passllm_environment = MODEL_ROOT / 'passllm_venv'
        if not passllm_environment.exists():
            run([uv, 'venv', '--python', '3.11', passllm_environment])
        passllm_python = passllm_environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run([uv, 'pip', 'install', '--python', passllm_python,
             '-r', ROOT / 'requirements-passllm.txt'])
    status = {model: source_status(model) for model in SOURCES}
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
