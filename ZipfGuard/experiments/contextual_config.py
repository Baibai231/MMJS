"""Separate configuration: never changes the published intervention presets."""
from copy import deepcopy
import math
from experiments.intervention_config import validate_intervention_config

DEFAULT = dict(protocol='contextual-candidate-search-v1', shortlist=12, reserved=4,
               screening_repeats=3, finalists=3, validation_repeats=30,
               beta=.5, ridge=1., mode='empirical', delta=.05,
               warmup_rounds=5, methods=['heuristic', 'random', 'greedy', 'bandit'],
               legacy=True)


def validate_config(value):
    if set(value) - {'intervention', 'learning'}:
        raise ValueError('Unknown contextual configuration key')
    base = validate_intervention_config(value.get('intervention', {}))
    opt = deepcopy(DEFAULT)
    supplied = value.get('learning', {})
    if not isinstance(supplied, dict) or set(supplied)-set(opt):
        raise ValueError('Unknown learning option')
    opt.update(supplied)
    if opt['protocol'] != DEFAULT['protocol'] or opt['mode'] not in ('empirical', 'strict'):
        raise ValueError('Unsupported learning protocol/mode')
    for key in ('shortlist', 'screening_repeats', 'finalists', 'validation_repeats'):
        if type(opt[key]) is not int or not 1 <= opt[key] <= 10000:
            raise ValueError(key)
    for key in ('reserved', 'warmup_rounds'):
        if type(opt[key]) is not int or not 0 <= opt[key] <= 200:
            raise ValueError(key)
    if opt['reserved'] > opt['shortlist'] or opt['finalists'] > opt['shortlist']:
        raise ValueError('Invalid shortlist sizes')
    for key in ('beta', 'ridge', 'delta'):
        x = opt[key]
        if type(x) not in (int, float) or not math.isfinite(x):
            raise ValueError(key)
    if opt['beta'] < 0 or opt['ridge'] <= 0 or not 0 < opt['delta'] < 1:
        raise ValueError('Invalid learning scalar')
    if (not isinstance(opt['methods'], list) or not opt['methods'] or
            len(set(opt['methods'])) != len(opt['methods']) or
            set(opt['methods'])-{'heuristic', 'random', 'greedy', 'bandit'}):
        raise ValueError('Invalid methods')
    if type(opt['legacy']) is not bool:
        raise ValueError('legacy must be bool')
    if base['controller']['execution_policy'] != 'dual-F-individual-threshold-v1':
        raise ValueError('Contextual experiment requires actual dual prefixes')
    return {'intervention': base, 'learning': opt}
