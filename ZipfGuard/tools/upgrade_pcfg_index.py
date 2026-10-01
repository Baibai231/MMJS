"""Add cached sample probabilities to an existing private MC index atomically."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.pcfg_monte_carlo import MonteCarloIndex


def main():
    parser = argparse.ArgumentParser(description='加速已保存 PCFG 索引的首次查询')
    parser.add_argument('files', nargs='+', type=Path)
    args = parser.parse_args()
    for path in args.files:
        path = path.resolve()
        index, population = MonteCarloIndex.load(path)
        temporary = path.with_suffix('.upgrade.tmp')
        try:
            index.save(temporary, population)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        print(path, flush=True)


if __name__ == '__main__':
    main()
