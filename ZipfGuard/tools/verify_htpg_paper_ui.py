"""Read-only browser QA for the strict HTPG surface and project navigation."""
import json
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from web.server import Handler


def main():
    directory = ROOT/'reports'/'htpg_paper'
    directory.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = 'http://127.0.0.1:'+str(server.server_port)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(origin+'/intervention/htpg')
            page.locator('#htpg-paper').wait_for()
            assert page.locator('h1').inner_text() == '论文 HTPG：严格复现核验'
            paper_row = page.locator('tr').filter(has_text='论文 HTPG · 严格组')
            assert paper_row.count() == 1
            assert paper_row.inner_text().count('未运行') == 7
            conditional_row = page.locator('tr').filter(has_text='HTPG 正文算法＋补全假设')
            assert conditional_row.count() == 1
            assert '18.489%' in conditional_row.inner_text()
            assert page.locator('.htpg-figure > svg').count() == 2
            assert '1172' in page.locator('body').inner_text()
            assert 'f > 3 的类别不足两个' in page.locator('body').inner_text()
            page.screenshot(path=str(directory/'ui_desktop.png'), full_page=True)
            response = page.request.get(origin+'/api/intervention/htpg/report')
            assert response.status == 200
            assert response.json()['report']['current_comparison']['comparison_is_complete'] is False
            conditional = page.request.get(origin+'/api/intervention/htpg/conditional')
            assert conditional.status == 200
            assert abs(conditional.json()['metrics']['A1_union_rate'] - .18489203125) < 1e-12
            for figure in ('conditional_distribution.svg', 'conditional_attack.svg'):
                assert page.request.get(origin+'/api/intervention/htpg/'+figure).status == 200
            response = page.request.post(origin+'/api/intervention/htpg/compute', data={'operation': 'run'})
            assert response.status == 409
            assert response.json()['paper_result'] is None
            page.set_viewport_size({'width': 390, 'height': 844})
            page.reload()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
            page.screenshot(path=str(directory/'ui_mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(origin+'/intervention')
            page.wait_for_load_state('networkidle')
            link = page.get_by_role('link', name='论文 HTPG 严格复现与对比')
            assert link.count() == 1
            link.click()
            page.locator('#htpg-paper').wait_for()
            assert not errors, errors
            browser.close()
        result = {'passed': True, 'desktop': [1440, 1000], 'mobile': [390, 844],
                  'checks': ['main workbench navigation', 'strict missing results',
                             'A1 conditional endpoint and two figures', 'actual report endpoint',
                             'strict run 409', 'mobile overflow', 'no page errors']}
        (directory/'ui_validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == '__main__':
    main()
