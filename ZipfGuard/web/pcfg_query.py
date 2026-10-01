"""Local-only, cached queries against artifacts from one completed run."""
import json
import re
import threading
from functools import lru_cache
from pathlib import Path
from ai.pcfg_monte_carlo import MonteCarloIndex, popularity
from policy.site_catalog import site_rules
from experiments.dynamic_config import ROOT

LOCK = threading.Lock()


@lru_cache(maxsize=2)
def _load(filename, size, modified_ns):
    return MonteCarloIndex.load(filename)


def query_saved(request):
    run_id, word = request.get('run_id', ''), request.get('password')
    if not re.fullmatch('[a-f0-9]{16}', run_id):
        raise ValueError('请先运行或加载本版实验结果')
    if not isinstance(word, str) or not 1 <= len(word) <= 1024 or any(ord(c) < 32 for c in word):
        raise ValueError('请输入 1 到 1024 字符的单行口令')
    level = request.get('level', 'F')
    if level not in ('F', 'A0', 'A1'):
        raise ValueError('未知攻击层次')
    from web.dynamic_interface import dynamic_report_directory
    directory = dynamic_report_directory(run_id)
    filename = directory / ('pcfg_adaptive_private.json' if level == 'A1' else 'pcfg_frozen_private.json')
    rule = None
    if level == 'A0':
        rule = site_rules(True).get(request.get('policy'))
        if rule is None:
            raise ValueError('A0 需要指定可执行的网站规则')
    with LOCK:
        stat = filename.stat()
        index, population = _load(str(filename), stat.st_size, stat.st_mtime_ns)
        result = {'guess': index.query(word, rule), 'popularity': popularity(word, population),
                  'population': '动态策略最终用户集' if level == 'A1' else '初始用户集',
                  'level': level, 'query_used_for_training': False}
    return result
