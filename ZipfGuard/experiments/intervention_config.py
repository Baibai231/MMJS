"""Independent, versioned configuration for existing-account interventions."""
from copy import deepcopy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = 'selective-intervention-v5-full-cdf-area'
DEFAULT = {
    'schema_version': PROTOCOL, 'seed': 42,
    'data': {'path': '../rockyou-withcount.txt', 'format': 'password_with_count',
             'encoding': 'latin-1', 'users': 1000, 'development': 3000},
    'budgets': [1, 10, 100, 1000, 10000, 100000, 1000000],
    'risk_budget': 1000000,
    'monte_carlo': {'samples': 10000, 'seed': 42},
    'pcfg': {'timeout_seconds': 180},
    'controller': {'round_fraction': .02, 'total_fraction': .20,
                   'batch_fractions': [.001, .002, .005, .01, .02], 'max_rounds': 40,
                   'max_groups': 12, 'popular_k': 20, 'lengths': [10, 12, 15],
                   'candidate_pool': 'site-fragments-1-18-v1',
                   'predictable_terms': ['gitlab', 'devops'],
                   'prediction_repeats': 3, 'min_gain': 0.000000000001,
                   'min_batch_fraction': .001, 'min_positive_trial_fraction': .6,
                   'max_a1_regression': .0005,
                   'combination_width': 6, 'validation_shortlist': 5,
                   'validation_repeats': 5, 'lookahead_width': 2,
                   'lookahead_rollouts': 2, 'bridge_max_first_loss': .0003,
                   'distribution_top_fraction': .01,
                   'candidate_shortlist': 80,
                   'target_relative_reduction': .10, 'stagnation_patience': 3,
                   'max_hhi_increase': .0001},
    'response': {'nonresponse': .15, 'weights': [.60, .25, .15], 'max_attempts': 5},
    'evaluation': {'adaptive': True, 'mutation_reference_limit': 10000},
}


def load_intervention_config(preset='intervention_smoke'):
    if preset not in ('intervention_smoke', 'intervention_full'):
        raise ValueError('未知局部干预预设')
    return validate_intervention_config(json.loads((ROOT / 'configs' / (preset + '.json')).read_text(encoding='utf-8')))


def validate_intervention_config(value):
    if not isinstance(value, dict):
        raise ValueError('配置必须是对象')
    cfg = deepcopy(DEFAULT)
    for key, item in value.items():
        if key not in cfg:
            raise ValueError('未知配置项：' + str(key))
        if isinstance(cfg[key], dict):
            if not isinstance(item, dict) or set(item) - set(cfg[key]):
                raise ValueError('配置项无效：' + key)
            cfg[key].update(item)
        else:
            cfg[key] = item
    if cfg['schema_version'] != PROTOCOL:
        raise ValueError('局部干预协议版本不匹配')

    def integer(v, low, high, name):
        if type(v) is not int or not low <= v <= high:
            raise ValueError(name + '超出允许范围')

    def number(v, low, high, name):
        if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(name + '超出允许范围')

    integer(cfg['seed'], 0, 2**32-1, '种子')
    d, c, r = cfg['data'], cfg['controller'], cfg['response']
    integer(d['users'], 1, 1000000, '账户数')
    integer(d['development'], 10, 1000000, '开发样本数')
    if not isinstance(d['path'], str) or not d['path'].strip():
        raise ValueError('请输入语料路径')
    if d['format'] not in ('password_with_count', 'raw_occurrences'):
        raise ValueError('语料格式应为带频次记录或逐行出现记录')
    if not isinstance(d['encoding'], str):
        raise ValueError('编码无效')
    ''.encode(d['encoding'])
    bs = cfg['budgets']
    if not isinstance(bs, list) or not bs or len(bs) > 20:
        raise ValueError('攻击次数刻度无效')
    for b in bs:
        integer(b, 1, 10**18, '猜测预算')
    integer(cfg['risk_budget'], 1, 10**18, '主评估预算')
    if bs != sorted(set(bs)) or cfg['risk_budget'] not in bs:
        raise ValueError('刻度须严格递增且包含主评估预算')
    integer(cfg['monte_carlo']['samples'], 100, 1000000, '采样数')
    integer(cfg['monte_carlo']['seed'], 0, 2**32-1, '采样种子')
    number(cfg['pcfg']['timeout_seconds'], 1, 7200, '训练超时')
    for k in ('round_fraction', 'total_fraction'):
        number(c[k], .000001, 1, k)
    if c['round_fraction'] > c['total_fraction']:
        raise ValueError('单轮上限不能超过累计上限')
    number(c['min_batch_fraction'], .000001, c['round_fraction'], '最小单轮比例')
    if math.floor(d['users'] * c['round_fraction'] + 1e-9) < 1:
        raise ValueError('单轮预算不足 1 个账户，请增加账户数或单轮比例')
    if not isinstance(c['batch_fractions'], list) or not 1 <= len(c['batch_fractions']) <= 8:
        raise ValueError('干预规模档位无效')
    for f in c['batch_fractions']:
        number(f, .000001, 1, '规模比例')
    for k, lo, hi in [('max_rounds', 1, 200), ('max_groups', 1, 50), ('popular_k', 1, 1000),
                      ('prediction_repeats', 1, 30), ('stagnation_patience', 1, 100),
                      ('combination_width', 1, 30), ('validation_shortlist', 1, 30),
                      ('validation_repeats', 1, 30), ('lookahead_width', 1, 10),
                      ('lookahead_rollouts', 1, 10)]:
        integer(c[k], lo, hi, k)
    integer(c['candidate_shortlist'], 1, 10000, '候选动作短名单')
    number(c['distribution_top_fraction'], .000001, 1, '分布前段比例')
    if not isinstance(c['lengths'], list) or not 1 <= len(c['lengths']) <= 8:
        raise ValueError('长度候选无效')
    for n in c['lengths']:
        integer(n, 1, 64, '长度')
    c['lengths'] = sorted(set(c['lengths']))
    if c['candidate_pool'] != 'site-fragments-1-18-v1':
        raise ValueError('候选池版本无效')
    if (not isinstance(c['predictable_terms'], list) or len(c['predictable_terms']) > 50
            or any(not isinstance(term, str) or not term or len(term) > 40
                   for term in c['predictable_terms'])):
        raise ValueError('可预测词表无效')
    c['predictable_terms'] = sorted(set(term.lower() for term in c['predictable_terms']))
    for k in ('min_gain', 'target_relative_reduction', 'max_hhi_increase'):
        number(c[k], 0, 1, k)
    for k in ('min_positive_trial_fraction', 'max_a1_regression',
              'bridge_max_first_loss'):
        number(c[k], 0, 1, k)
    number(r['nonresponse'], 0, 1, '未响应概率')
    if not isinstance(r['weights'], list) or len(r['weights']) != 3:
        raise ValueError('响应权重必须有三项')
    for w in r['weights']:
        number(w, 0, 1, '响应权重')
    if abs(sum(r['weights']) - 1) > 1e-9:
        raise ValueError('响应权重之和须为 1')
    integer(r['max_attempts'], 1, 30, '最大尝试数')
    if type(cfg['evaluation']['adaptive']) is not bool:
        raise ValueError('适应攻击开关须为布尔值')
    integer(cfg['evaluation']['mutation_reference_limit'], 1, 100000, '变换参考词条数')
    return cfg
