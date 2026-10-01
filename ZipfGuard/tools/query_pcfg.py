"""Query a saved private PCFG index without retraining or re-sampling."""
import argparse
import getpass
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ai.pcfg_monte_carlo import MonteCarloIndex, popularity
from policy.site_catalog import site_rules


def main():
    parser = argparse.ArgumentParser(description='查询 PCFG 猜测次数估计及用户集热门排名')
    parser.add_argument('--index', required=True, type=Path)
    parser.add_argument('--policy', choices=sorted(site_rules(True)), help='A0：已知网站规则')
    parser.add_argument('--stdin', action='store_true', help='每行一条口令，从标准输入读取')
    args = parser.parse_args()
    index, population = MonteCarloIndex.load(args.index)
    rule = site_rules(True)[args.policy] if args.policy else None
    words = (line.rstrip('\r\n') for line in sys.stdin) if args.stdin else [getpass.getpass('待查询口令（隐藏输入）：')]
    for word in words:
        print(json.dumps({'guess': index.query(word, rule), 'popularity': popularity(word, population)},
                         ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
