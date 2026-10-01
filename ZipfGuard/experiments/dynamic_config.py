"""Validated presets for the simulated sequential-registration study."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from experiments.open_config import load_open_config, validate_open_config

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'zipfguard-dynamic-v1'
COMPARISON_PROTOCOL = 'fixed-preset-three-attacks-v2'
MC_COMPARISON_PROTOCOL = 'top15-pcfg-monte-carlo-v1'


def load_dynamic_config(preset='dynamic_smoke', path=None):
    if preset not in ('dynamic_smoke', 'dynamic_full') and path is None:
        raise ValueError('未知动态实验预设')
    source = Path(path) if path else ROOT / 'configs' / f'{preset}.json'
    return validate_dynamic_config(json.loads(source.read_text(encoding='utf-8')))


def validate_dynamic_config(value):
    cfg = copy.deepcopy(value)
    if not isinstance(cfg, dict) or set(cfg) - {'research_models', 'monte_carlo'} != {'schema_version', 'seed', 'data',
                                                 'budgets', 'attackers', 'generation',
                                                 'pcfg', 'controller', 'controls'}:
        raise ValueError('动态实验配置字段不匹配')
    if cfg['schema_version'] != SCHEMA:
        raise ValueError('动态实验版本不匹配')
    d = cfg['data']
    if set(d) != {'path', 'format', 'encoding', 'users', 'development', 'cohort_size'}:
        raise ValueError('动态数据配置字段不匹配')
    if d['format'] not in ('password_with_count', 'raw_occurrences'):
        raise ValueError('动态实验需要出现频次')
    if not isinstance(d['path'], str) or not d['path']:
        raise ValueError('需要语料路径')
    import codecs
    codecs.lookup(d['encoding'])
    for name, low, high in [('seed', 0, 2**32-1)]:
        if type(cfg[name]) is not int or not low <= cfg[name] <= high:
            raise ValueError(name)
    for key, low in [('users', 1), ('development', 3), ('cohort_size', 1)]:
        if type(d[key]) is not int or d[key] < low or d[key] > 2_000_000:
            raise ValueError(f'data.{key}')
    mc = cfg.get('monte_carlo')
    if mc is not None:
        if set(mc) != {'samples', 'seed'} or type(mc['samples']) is not int or not 100 <= mc['samples'] <= 2_000_000 or type(mc['seed']) is not int or not 0 <= mc['seed'] < 2**32:
            raise ValueError('蒙特卡洛样本数或种子无效')
        if cfg['attackers'] != {'pcfg': 'required'}:
            raise ValueError('蒙特卡洛主实验只使用 PCFG')
    budget_limit = 10**18 if mc else 1_000_000
    if (not isinstance(cfg['budgets'], list) or not cfg['budgets']
            or any(type(k) is not int or not 1 <= k <= budget_limit for k in cfg['budgets'])):
        raise ValueError('budgets 须为 1..1,000,000 的数组')
    cfg['budgets'] = sorted(set(cfg['budgets']))
    if (not isinstance(cfg['attackers'], dict) or not cfg['attackers']
            or not set(cfg['attackers']) <= {'frequency', 'dictionary-rules',
                                            'character-ngram', 'pcfg', 'omen', 'passgpt', 'passllm'}
            or any(mode not in ('required', 'optional')
                   for mode in cfg['attackers'].values())
            or 'required' not in cfg['attackers'].values()):
        raise ValueError('攻击器配置无效')
    if set(cfg['generation']) != {'raw_limit', 'timeout_seconds',
                                   'max_frontier', 'max_expansions'}:
        raise ValueError('生成资源配置字段不匹配')
    if set(cfg['pcfg']) != {'raw_limit', 'timeout_seconds', 'training_limit'}:
        raise ValueError('PCFG 资源配置字段不匹配')
    # Migrate saved v1 settings to the informed-generation protocol. Old reports
    # retain their original metadata and are never relabeled as new results.
    c = cfg['controller']
    if not isinstance(c, dict):
        raise ValueError('控制器配置无效')
    legacy_attempts = c.pop('max_attempts', 8)
    c.pop('abandon_probability', None)
    c.pop('min_completion', None)
    c.setdefault('retry_report_after', legacy_attempts)
    c.setdefault('candidate_limit', 1000)
    c.setdefault('candidate_source', 'legacy')
    c.setdefault('include_recommendations', False)
    c.setdefault('sequence', None)
    if c['candidate_source'] not in ('legacy', 'top15') or type(c['include_recommendations']) is not bool:
        raise ValueError('网站策略配置无效')
    if set(c) != {'candidate_source', 'include_recommendations', 'sequence', 'retry_report_after', 'candidate_limit', 'response_weights',
                                  'max_modification_rate',
                                  'max_incremental_modification',
                                  'max_late_cost_increase',
                                  'max_head_risk_regression',
                                  'min_relative_collision_gain',
                                  'preview_size', 'include_pattern_rules'}:
        raise ValueError('控制器配置字段不匹配')
    comparison_protocol = MC_COMPARISON_PROTOCOL if mc else COMPARISON_PROTOCOL
    if mc and cfg['controls'].get('comparison_protocol') == COMPARISON_PROTOCOL:
        cfg['controls']['comparison_protocol'] = comparison_protocol
    cfg['controls'].setdefault('comparison_protocol', comparison_protocol)
    if cfg['controls']['comparison_protocol'] == 'common-first-cost-batch-forecast-v1':
        cfg['controls']['comparison_protocol'] = COMPARISON_PROTOCOL
    cfg['controls'].setdefault('preset', 'length8')
    if (set(cfg['controls']) != {'run', 'attack', 'comparison_protocol', 'preset'} or any(
            type(cfg['controls'][key]) is not bool for key in ('run', 'attack'))
            or cfg['controls']['comparison_protocol'] != comparison_protocol
            or cfg['controls']['preset'] not in ('length8', 'top15')):
        raise ValueError('对照配置无效')
    if c['sequence'] is not None:
        from policy.site_catalog import site_rules
        expected = (d['users'] + d['cohort_size'] - 1) // d['cohort_size']
        allowed = site_rules(c['include_recommendations'])
        if (c['candidate_source'] != 'top15' or cfg['controls']['preset'] != 'top15'
                or not isinstance(c['sequence'], list) or len(c['sequence']) != expected
                or any(type(name) is not str or name not in allowed for name in c['sequence'])):
            raise ValueError('十批策略序列无效或含未证实网站规则')
    if (type(c['retry_report_after']) is not int or type(c['candidate_limit']) is not int
            or not 1 <= c['retry_report_after'] <= c['candidate_limit'] <= 10_000):
        raise ValueError('重试报告阈值或候选计算上限无效')
    if type(c['preview_size']) is not int or not 10 <= c['preview_size'] <= 100_000:
        raise ValueError('preview_size')
    if type(c['include_pattern_rules']) is not bool:
        raise ValueError('include_pattern_rules')
    for key in ('max_modification_rate',
                'max_incremental_modification', 'max_late_cost_increase',
                'max_head_risk_regression',
                'min_relative_collision_gain'):
        v = c[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
            raise ValueError(key)
    if (not isinstance(c['response_weights'], list)
            or len(c['response_weights']) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or v < 0 for v in c['response_weights'])
            or abs(sum(c['response_weights'])-1) > 1e-9):
        raise ValueError('response_weights')
    attack = load_open_config('open_full')
    attack['seed'] = cfg['seed']
    attack['budgets'] = [1] if mc else cfg['budgets']
    attack['attackers'] = cfg['attackers']
    attack['generation'] = cfg['generation']
    attack['pcfg'] = cfg['pcfg']
    if 'research_models' in cfg:
        attack['research_models'] = cfg['research_models']
    attack['search']['risk_budget'] = attack['budgets'][-1]
    # PCFG's current upstream adapter has a one-million raw-output ceiling.
    validate_open_config(attack)
    return cfg


def open_attack_config(cfg):
    attack = load_open_config('open_full')
    attack.update(seed=cfg['seed'], budgets=cfg['budgets'],
                  attackers=cfg['attackers'], generation=cfg['generation'],
                  pcfg=cfg['pcfg'])
    attack['search']['risk_budget'] = cfg['budgets'][-1]
    if 'research_models' in cfg:
        attack['research_models'] = cfg['research_models']
    return validate_open_config(attack)
