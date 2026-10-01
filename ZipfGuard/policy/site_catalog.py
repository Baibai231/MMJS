"""Traceable projections of the team's fifteen-site document, without invented rules."""
import json
from pathlib import Path
from policy.open_policy import Rule

CATALOG = Path(__file__).resolve().parents[1] / 'configs/top15_sites.json'


def site_catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def site_rules(include_recommendations=False):
    result = {}
    for row in site_catalog()['sites']:
        if row['basis'] == 'partial_hard' or (include_recommendations and row['basis'] == 'recommended_scenario'):
            result['site_' + row['id']] = Rule('site_' + row['id'], origin=row['basis'], **row['rule'])
    return result


def site_labels():
    return {'site_' + r['id']: r['site'] + ('（建议采纳场景）' if r['basis'] == 'recommended_scenario' else '（已知规则子集）')
            for r in site_catalog()['sites']}
