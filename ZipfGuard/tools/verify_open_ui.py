"""Actual browser acceptance of open UI, jobs, downloads and aggregate errors."""
import argparse
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.data import synthetic_counts
from experiments.open_config import load_open_config


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--url',default='http://127.0.0.1:8766');args=ap.parse_args()
    out=ROOT/'reports';out.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(args.url);page.wait_for_function("document.getElementById('seed').value === '42'")
        page.locator('#preset').select_option('open_full')
        page.wait_for_function("document.getElementById('size').value === '20000'")
        assert page.locator('#att-pcfg').input_value()=='optional'
        page.locator('#preset').select_option('open_quick')
        page.wait_for_function("document.getElementById('size').value === '2000'")
        page.screenshot(path=str(out/'open_controls.png'),full_page=True)
        # Complete an actual, bounded real-corpus experiment through the browser.
        page.locator('#size').fill('400')
        page.locator('#budgets').fill('10,100');page.locator('#riskbudget').fill('100')
        page.locator('#advanced > summary').click()
        page.locator('#run').click()
        page.wait_for_function("document.getElementById('status').textContent === '实验完成'",timeout=900000)
        assert page.locator('#result svg').count()>=3
        for label in ('需求对应的推荐','自适应风险与预算','用户响应与攻击者适应','分布怎样参与规则发现'):
            assert page.get_by_role('heading',name=label,exact=True).count()==1
        report=page.evaluate('last.result')
        assert report['protocol_id']=='open-minauto-v1'
        assert len(report['metadata']['participating_attackers'])==3
        assert report['dataset']['synthetic'] is False
        assert report['dataset']['source_rows']>1000000
        assert report['protocol']['candidate_allowlist'] is False
        page.locator('#result').screenshot(path=str(out/'open_results.png'))
        with page.expect_download() as downloaded:page.locator('#html-download').click()
        assert downloaded.value.suggested_filename=='zipfguard_open.html'
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(out/'open_mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('#source').select_option('upload')
        page.locator('#upload').set_input_files({'name':'counts.json','mimeType':'application/json','buffer':json.dumps(synthetic_counts(size=100,categories=10)).encode()})
        page.locator('#run').click()
        page.wait_for_function("document.getElementById('status').textContent === '实验完成'",timeout=60000)
        assert '均未运行' in page.locator('#result').inner_text()
        assert page.get_by_role('heading',name='需求对应的推荐').count()==0
        page.locator('#upload').set_input_files({'name':'bad.json','mimeType':'application/json','buffer':b'bad'})
        page.locator('#run').click()
        page.wait_for_function("document.getElementById('status').textContent.startsWith('未完成')")
        assert page.locator('#result').inner_text()==''
        assert page.locator('#download').is_disabled()
        assert not errors,errors
        summary={'browser':browser.version,'javascript_errors':errors,'protocol':report['protocol_id'],
                 'source_rows':report['dataset']['source_rows'],'sample_occurrences':report['dataset']['sample_occurrences'],
                 'runtime_seconds':report['metadata']['runtime_seconds'],
                 'checks':['presets','actual real corpus job','recommendations and plots','HTML download','mobile overflow',
                           'aggregate-only analysis','bad upload clears stale results']}
        (out/'open_browser_checks.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=False));browser.close()

if __name__=='__main__':main()
