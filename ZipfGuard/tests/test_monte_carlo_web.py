"""Local HTTP query/UI check against a saved MC report, without corpus disclosure."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from web.server import Handler, ROOT


class MonteCarloWebTests(unittest.TestCase):
    def test_local_query_and_attack_plot(self):
        reports = sorted((ROOT / 'reports/dynamic').glob('*/report.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        reports = [p for p in reports if 'monte_carlo' in json.loads(p.read_text(encoding='utf-8')).get('config', {})]
        if not reports:
            self.skipTest('Run an MC smoke experiment first')
        browser_path = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
        if not browser_path.is_file():
            self.skipTest('Existing browser unavailable')
        from playwright.sync_api import sync_playwright
        private = json.loads((reports[0].parent / 'pcfg_frozen_private.json').read_text(encoding='utf-8'))
        word = next(iter(private['population']))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=str(browser_path), headless=True)
                page = browser.new_page(viewport={'width': 1400, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto('http://127.0.0.1:' + str(server.server_port))
                page.wait_for_selector('#attack-success svg')
                self.assertEqual(page.locator('#candidate-pool').count(), 0)
                page.locator('#sequence-mode').select_option('explicit')
                self.assertEqual(page.locator('#sequence-controls select').count(), 10)
                page.locator('#sequence-controls select').first.select_option('site_instagram')
                self.assertEqual(page.evaluate('get().controller.sequence[0]'), 'site_instagram')
                self.assertEqual(page.locator('#attack-success svg[role=img]').count(), 3)
                self.assertIn('10¹²', page.locator('#attack-success').inner_text())
                self.assertEqual(page.locator('#policy-rankings svg[role=img]').count(), 3)
                current = json.loads(reports[0].read_text(encoding='utf-8'))
                if current['config']['controller'].get('sequence') is None:
                    self.assertIn('当前逐批控制器为何保持首批预设', page.locator('#policy-rankings').inner_text())
                else:
                    self.assertIn('本次指定路径的性质', page.locator('#policy-rankings').inner_text())
                    self.assertEqual(page.locator('#sequence-rankings svg[role=img]').count(), 3)
                ranking_url = page.locator('#policy-rankings a.figure-link').first.get_attribute('href')
                self.assertIn('/api/dynamic/figure/', ranking_url)
                ranking = page.request.get('http://127.0.0.1:' + str(server.server_port) + ranking_url)
                self.assertEqual(ranking.status, 200)
                self.assertIn('image/svg+xml', ranking.headers['content-type'])
                figure_url = page.locator('#attack-success a.figure-link').first.get_attribute('href')
                self.assertIn('/api/dynamic/figure/', figure_url)
                figure = page.request.get('http://127.0.0.1:' + str(server.server_port) + figure_url)
                self.assertEqual(figure.status, 200)
                self.assertIn('image/svg+xml', figure.headers['content-type'])
                page.locator('#query-password').fill(word)
                page.locator('#query').click()
                page.wait_for_function("document.getElementById('query-result').textContent.includes('热门排名')")
                self.assertIn('出现', page.locator('#query-result').inner_text())
                page.locator('#query-password').fill('')
                page.locator('#attack-success').screenshot(path=str(ROOT / 'reports/pcfg_mc_attack_ui.png'))
                self.assertEqual(errors, [])
                browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
