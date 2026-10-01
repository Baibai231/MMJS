"""Run the third-workbench experiment without opening a browser."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from experiments.intervention_config import load_intervention_config
from experiments.intervention_pipeline import run_intervention_pipeline


def main():
    parser = argparse.ArgumentParser(description='存量账户局部干预实验')
    parser.add_argument('--preset', default='intervention_smoke', choices=['intervention_smoke', 'intervention_full'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding='utf-8')) if args.config else load_intervention_config(args.preset)
    report = run_intervention_pipeline(cfg, output_dir=args.output_dir, progress=lambda text: print(text, flush=True))
    print('完成：', report['metadata']['run_id'])
    for arm in report['arms'].values():
        print(arm['label'], arm['final']['ledger'], arm['stop_label'])


if __name__ == '__main__':
    main()
