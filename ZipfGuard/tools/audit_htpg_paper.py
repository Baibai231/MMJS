"""Run the strict paper audit without pretending missing definitions are known."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.htpg_paper_audit import OUTPUT, build_audit, save_audit


def main():
    parser = argparse.ArgumentParser(description='核验论文 HTPG，记录正文缺口并接入当前方法对比')
    parser.add_argument('--paper', type=Path)
    parser.add_argument('--corpus', type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = build_audit(paper_path=args.paper, corpus_path=args.corpus,
                         run_id=args.run_id, progress=lambda value: print(value, flush=True))
    directory = save_audit(report, args.output)
    print('保存核验报告：'+str(directory), flush=True)
    print('严格 HTPG 终态未执行：正文缺少原始特征编码、词表和完整修改函数。', flush=True)
    if report['corpus_reproduction']:
        fit = report['corpus_reproduction']['fit']
        print(f'C={fit["C"]:.6f} alpha={fit["alpha"]:.9f} x0={fit["curvature_x0"]:.6f} cutoff={fit["cutoff_rank"]}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
