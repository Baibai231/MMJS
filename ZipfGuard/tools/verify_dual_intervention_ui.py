"""Verify saved dual-attack reports in a browser without starting experiments."""
import argparse
import json
import shutil
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from web.intervention_interface import published
from web.server import Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.report_dir/'report.json').read_text(encoding='utf-8'))
    assert report['baseline']['risk'].get('attack_curves'), 'Requires a dual-attack report'
    output = published(report)
    run_id = output['run_id']
    out = ROOT/'reports'/'dual_ui_validation'
    out.mkdir(parents=True, exist_ok=True)
    names = ['attack_F', 'attack_A1', 'attack_markov_F', 'attack_markov_A1',
             'attack_union_F', 'attack_union_A1']
    with tempfile.TemporaryDirectory(dir=out) as temporary:
        fixture = Path(temporary)
        assets = fixture/'reports'/'intervention'/run_id
        assets.mkdir(parents=True)
        for path in [args.report_dir/'report.json', *args.report_dir.glob('*.svg')]:
            shutil.copy2(path, assets/path.name)
        with patch('web.server.ROOT', fixture), patch(
                'web.server.latest_intervention_result', return_value={'output': output}), patch(
                'web.intervention_interface.latest_intervention_result', return_value={'output': output}), patch(
                'web.server.intervention_activity', return_value=None):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            origin = 'http://127.0.0.1:'+str(server.server_port)
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless=True)
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
                    errors = []
                    page.on('pageerror', lambda exc: errors.append(str(exc)))
                    page.goto(origin+'/intervention')
                    page.wait_for_function("document.getElementById('users').value !== ''")
                    assert page.locator('#result').get_attribute('data-run-id') == run_id
                    for name in names:
                        panel = page.locator('#'+name)
                        assert panel.count() == 1, name
                        assert panel.locator('svg').count() >= 1, name
                        response = page.request.get(origin+'/api/intervention/figure/'+run_id+'/'+name+'.svg')
                        assert response.status == 200 and '图例' in response.text(), name
                    chart = page.locator('#attack_union_F .plot-distinct')
                    checkbox = chart.locator('.series-key-1 input')
                    checkbox.uncheck()
                    assert chart.locator('svg > .series-1').evaluate('e=>getComputedStyle(e).display') == 'none'
                    checkbox.check()
                    for anchor in ('intervention-overview', 'ideal_distribution_reference',
                                   'intervention-rounds', 'round_parameters', 'distribution-fit'):
                        assert page.locator('#'+anchor).count() == 1, anchor
                    if report.get('site_controls', {}).get('yahoo_japan'):
                        assert page.locator('#yahoo_control').count() == 1
                        for name in ('yahoo_attack_F', 'yahoo_attack_A1'):
                            assert page.request.get(origin+'/api/intervention/figure/'+run_id+'/'+name+'.svg').status == 200
                    with page.expect_download() as download:
                        page.locator('#download').click()
                    downloaded = json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
                    assert downloaded['metadata']['run_id'] == run_id
                    page.locator('#intervention-overview').screenshot(path=str(out/'overview.png'))
                    page.locator('#attack_union_F').screenshot(path=str(out/'attack_union_F.png'))
                    assert not errors, errors
                    browser.close()
                    (out/'validation.json').write_text(json.dumps(
                        {'passed': True, 'run_id': run_id, 'charts': names,
                         'checks': ['page rendering', 'SVG routes', 'interactive legend', 'report download', 'no JS errors']},
                        ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
                    print('Browser validation passed:', run_id)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    main()
