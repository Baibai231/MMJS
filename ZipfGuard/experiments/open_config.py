"""Versioned configuration for the real-corpus open Min_auto experiment."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'zipfguard-open-v2'
PROTOCOL = 'open-minauto-v1'
FEATURES = ('year_suffix', 'keyboard_walk', 'repeated', 'sequential_digits', 'word_plus_digits')


def load_open_config(preset='open_quick', path=None):
    if preset not in ('open_quick', 'open_full') and path is None:
        raise ValueError('未知开放实验预设')
    return validate_open_config(json.loads((Path(path) if path else ROOT/'configs'/f'{preset}.json').read_text('utf-8')))


def validate_open_config(value):
    c = copy.deepcopy(value)
    required = {'schema_version','seed','data','budgets','bootstrap_repetitions','attackers',
                'generation','ngram','pcfg','response','search','knowledge','discovery'}
    if not isinstance(c, dict) or set(c) != required or c.get('schema_version') != SCHEMA:
        raise ValueError('开放实验配置字段或版本不匹配')
    sections = {
        'data': {'path','format','encoding','sample_size','split_fractions'},
        'generation': {'raw_limit','timeout_seconds','max_frontier','max_expansions'},
        'ngram': {'orders','smoothing','min_length','max_length'},
        'pcfg': {'timeout_seconds','raw_limit','training_limit'},
        'response': {'scenarios','primary','max_attempts','r0_max_total_attempts','reselect_probability','abandon_probability'},
        'search': {'risk_budget','blocklist_size','lengths','max_rules','min_gain','requirements'},
        'discovery': {'mode','max_features','head_mass','min_support'},
    }
    for section, keys in sections.items():
        if not isinstance(c[section],dict) or set(c[section]) != keys:
            raise ValueError(f'{section} 配置字段不匹配')
    for section,key in (('data','split_fractions'),('ngram','orders'),('ngram','smoothing'),
                        ('response','scenarios'),('search','lengths'),('search','requirements')):
        if not isinstance(c[section][key],list):raise ValueError(f'{section}.{key} 须为数组')
    if not isinstance(c['budgets'],list) or not isinstance(c['attackers'],dict):
        raise ValueError('budgets 须为数组，attackers 须为对象')
    requirement_keys={'name','mode','max_cost','max_risk','min_completion','max_extra_attempts'}
    for requirement in c['search']['requirements']:
        if not isinstance(requirement,dict) or set(requirement)!=requirement_keys:
            raise ValueError('安全需求字段不匹配')
        name=requirement['name']
        if not isinstance(name,str) or not name.strip() or len(name)>80 or any(ord(x)<32 for x in name):
            raise ValueError('需求名称须为 1..80 个可打印字符')
    def integer(v, lo, hi, name):
        if type(v) is not int or not lo <= v <= hi: raise ValueError(f'{name} 须为 {lo}..{hi} 整数')
    def real(v, lo, hi, name):
        if isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f'{name} 须位于 [{lo},{hi}]')
    integer(c['seed'],0,2**32-1,'seed'); integer(c['bootstrap_repetitions'],20,2000,'bootstrap')
    d=c['data']
    if d['format'] not in ('password_with_count','raw_occurrences','unique_dictionary'): raise ValueError('未知数据格式')
    if not isinstance(d['path'], str) or not d['path']: raise ValueError('需要本地语料路径')
    import codecs
    try: codecs.lookup(d['encoding'])
    except (LookupError,TypeError): raise ValueError('未知字符编码') from None
    integer(d['sample_size'],40,200000,'sample_size')
    if len(d['split_fractions']) != 4: raise ValueError('需要 train/tuning/validation/test 四个比例')
    for f in d['split_fractions']: real(f,.01,.97,'split_fraction')
    if abs(sum(d['split_fractions'])-1)>1e-8: raise ValueError('划分比例之和须为 1')
    if not c['budgets']: raise ValueError('至少一个预算')
    for k in c['budgets']: integer(k,1,1000000,'budget')
    c['budgets']=sorted(set(c['budgets']))
    if not c['attackers'] or not set(c['attackers']) <= {'frequency','dictionary-rules','character-ngram','pcfg'}:
        raise ValueError('请选择已接入的开放攻击器')
    if any(v not in ('required','optional') for v in c['attackers'].values()) or 'required' not in c['attackers'].values():
        raise ValueError('攻击器须为 required/optional，且至少一个必选')
    g=c['generation']
    integer(g['raw_limit'],max(c['budgets']),10000000,'raw_limit')
    integer(g['timeout_seconds'],1,3600,'generation timeout')
    integer(g['max_frontier'],100,2000000,'max_frontier')
    integer(g['max_expansions'],100,10000000,'max_expansions')
    integer(c['ngram']['min_length'],1,32,'ngram min_length')
    integer(c['ngram']['max_length'],c['ngram']['min_length'],64,'ngram max_length')
    for order in c['ngram']['orders']: integer(order,1,5,'ngram order')
    for alpha in c['ngram']['smoothing']: real(alpha,.0001,10,'smoothing')
    if not c['ngram']['orders'] or not c['ngram']['smoothing']: raise ValueError('n-gram 参数不能为空')
    integer(c['pcfg']['timeout_seconds'],1,3600,'pcfg timeout')
    integer(c['pcfg']['raw_limit'],max(c['budgets']),1000000,'pcfg raw_limit')
    integer(c['pcfg']['training_limit'],40,500000,'pcfg training_limit')
    r=c['response']
    if not r['scenarios'] or not set(r['scenarios']) <= {'R0','R1','R2'} or r['primary'] not in r['scenarios']:
        raise ValueError('响应情景须包含主情景 R0/R1/R2')
    if len(set(r['scenarios'])) != len(r['scenarios']): raise ValueError('响应情景不能重复')
    integer(r['max_attempts'],1,30,'max_attempts')
    integer(r['r0_max_total_attempts'],0,100,'r0_max_total_attempts')
    real(r['reselect_probability'],0,1,'reselect_probability');real(r['abandon_probability'],0,1,'abandon_probability')
    s=c['search'];integer(s['risk_budget'],1,1000000,'risk_budget')
    if s['risk_budget'] not in c['budgets']: raise ValueError('选择预算必须包含在攻击预算中')
    integer(s['blocklist_size'],1,100000,'blocklist_size');integer(s['max_rules'],1,5,'max_rules')
    for n in s['lengths']: integer(n,1,64,'length threshold')
    if not s['lengths'] or len(s['lengths'])>8: raise ValueError('长度对照须为 1..8 档')
    if len(set(s['lengths']))!=len(s['lengths']): raise ValueError('长度不能重复')
    if not 1 <= len(s['requirements']) <= 8: raise ValueError('需要 1..8 个安全需求')
    if len({t['name'] for t in s['requirements']})!=len(s['requirements']): raise ValueError('需求名称不能重复')
    for t in s['requirements']:
        if t['mode'] not in ('cost_cap','risk_target'): raise ValueError('未知需求模式')
        for key in ('max_cost','max_risk','min_completion'): real(t[key],0,1,key)
        real(t['max_extra_attempts'],0,1000,'max_extra_attempts')
    real(s['min_gain'],0,1,'min_gain')
    if c['knowledge'] != ['F','A0','A1']: raise ValueError('当前实现比较固定的 F/A0/A1；A2 未接入')
    if c['discovery']['mode'] not in ('distribution','none'): raise ValueError('未知候选发现模式')
    integer(c['discovery']['max_features'],1,len(FEATURES),'max_features')
    real(c['discovery']['head_mass'],.01,.99,'head_mass');real(c['discovery']['min_support'],0,1,'min_support')
    json.dumps(c,allow_nan=False)
    return c
