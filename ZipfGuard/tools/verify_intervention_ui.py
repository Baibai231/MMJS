"""Browser acceptance of the third workbench with saved real results and a tiny job."""
import json
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web.server import Handler
from experiments.intervention_config import load_intervention_config
from experiments.intervention_pipeline import run_intervention_pipeline


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = 'http://127.0.0.1:'+str(server.server_port)
    out = ROOT/'reports'/'intervention_ui'
    out.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p, tempfile.TemporaryDirectory() as temp, patch(
                'web.intervention_interface.run_intervention_pipeline',
                side_effect=lambda cfg, **kwargs: run_intervention_pipeline(cfg, output_dir=Path(temp)/'report', **kwargs)):
            browser = p.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(origin+'/intervention')
            page.wait_for_function("document.getElementById('users').value !== ''")
            page.wait_for_function("!document.getElementById('status').textContent.includes('读取')")
            assert '第三展示台' in page.title()
            assert page.locator('nav[aria-label="实验结果导航"] a').count() >= 8
            latest_run = page.request.get(origin+'/api/intervention/latest').json()['output']['run_id']
            assert page.locator('#result').get_attribute('data-run-id') == latest_run
            if page.locator('#risk_cost').count():
                page.locator('#intervention-overview').screenshot(path=str(out/'overview.png'))
                page.locator('#risk_cost').screenshot(path=str(out/'risk_cost.png'))
                chart = page.locator('#risk_cost .plot-distinct')
                checkbox = chart.locator('.series-key-1 input')
                checkbox.uncheck()
                assert chart.locator('svg > .series-1').evaluate("e=>getComputedStyle(e).display") == 'none'
                checkbox.check()
                chart.locator('.series-key-1').hover()
                assert float(chart.locator('svg > .series-2').evaluate("e=>getComputedStyle(e).opacity")) < .1
                page.get_by_role('heading', name='每轮具体做了什么').hover()
                figure_url = page.locator('#risk_cost a').get_attribute('href')
                svg = page.request.get(origin+figure_url)
                assert svg.status == 200 and '图例' in svg.text()
                with page.expect_download() as download:
                    page.locator('#download').click()
                assert download.value.suggested_filename == 'intervention_report.json'
                report = json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
                assert report['schema_version'] == 'selective-intervention-v1-result'
                assert report['dataset']['source_rows'] > 1000000
                comparison = report['google_round_zipf']
                assert comparison['control']['state_sha256'] == comparison['experimental']['start_state_sha256']
                assert comparison['experimental']['rounds_completed'] == 10
                expected = ['无政策', 'Google 政策不变', 'Google 起点＋10 轮逐步干预']
                for name in ('risk_cost', 'guarded_cost', 'attack_F', 'attack_A1', 'attack_mutations', 'final_distribution'):
                    panel = page.locator('#'+name)
                    assert panel.locator('.legend label span').all_text_contents() == expected
                    assert panel.locator('svg > .plot-series').count() == 3
                    exported = page.request.get(origin+panel.locator('a').get_attribute('href'))
                    assert exported.status == 200
                    assert all(label in exported.text() for label in expected)
                    assert '初始一次规划' not in exported.text()
                page.locator('#attack_A1').screenshot(path=str(out/'attack_A1.png'))
                page.locator('#final_distribution').screenshot(path=str(out/'final_distribution.png'))
                zipf = page.locator('#google_round_zipf .plot-distinct')
                assert zipf.locator('.legend input[type="checkbox"]').count() == 11
                assert zipf.locator('svg > .plot-series').count() == 11
                last = zipf.locator('.series-key-10 input')
                last.uncheck()
                assert zipf.locator('svg > .series-10').evaluate("e=>getComputedStyle(e).display") == 'none'
                last.check()
                zipf_url = page.locator('#google_round_zipf a').get_attribute('href')
                zipf_svg = page.request.get(origin+zipf_url)
                assert zipf_svg.status == 200 and 'Google 政策不变' in zipf_svg.text()
                page.locator('#google_round_zipf').screenshot(path=str(out/'google_round_zipf.png'))
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
                page.screenshot(path=str(out/'mobile.png'))
                page.set_viewport_size({'width': 1440, 'height': 1000})
            else:
                raise AssertionError('Run the real intervention_smoke experiment before browser acceptance')
            # Real backend through browser, but use a bounded counted fixture to
            # avoid rescanning the entire corpus for UI lifecycle checks.
            corpus = Path(temp)/'counted.txt'
            corpus.write_text('100 hello123\n100 world456\n100 summer2026!\n100 abc123\n100 qwerty\n', encoding='ascii')
            cfg = load_intervention_config()
            cfg['data'].update(path=str(corpus), users=100, development=100)
            cfg['monte_carlo']['samples'] = 1000
            cfg['controller'].update(max_rounds=2, max_groups=3, prediction_repeats=1)
            cfg['evaluation']['adaptive'] = False
            page.evaluate('(cfg)=>fill(cfg)', cfg)
            page.locator('#run').click()
            page.wait_for_function("document.getElementById('status').textContent === '局部干预实验完成'", timeout=180000)
            assert page.locator('#risk_cost svg[role=img]').count() == 1
            assert page.locator('#attack_A1').count() == 0
            page.locator('#total').fill('0')
            page.locator('#run').click()
            page.wait_for_function("document.getElementById('status').textContent.startsWith('实验未完成')")
            assert page.locator('#download').is_disabled()
            assert page.locator('#risk_cost').count() == 0
            bad = page.request.post(origin+'/api/intervention/jobs', data=json.dumps({'config': {}}),
                                    headers={'Content-Type':'application/json', 'Sec-Fetch-Site':'cross-site'})
            assert bad.status == 403
            assert page.request.get(origin+'/api/intervention/report/../../README.md').status == 404
            for endpoint in ['/open', '/dynamic']:
                response = page.request.get(origin+endpoint)
                assert response.status == 200 and '/intervention' in response.text()
            assert not errors, errors
            summary = {'browser': browser.version, 'javascript_errors': errors,
                       'checks': ['real report', 'shared legend toggle and hover',
                                  'six consistent three-strategy charts', '11-line Google-started Zipf chart', 'SVG legend export',
                                  'report download', 'mobile containment', 'background job',
                                  'invalid config clears stale results', 'cross-site rejection',
                                  'asset path boundary', 'single third workbench navigation']}
            (out/'checks.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            print(json.dumps(summary, ensure_ascii=False))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
