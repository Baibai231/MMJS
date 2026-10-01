"""Exercise the published pilot without starting any new experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8766')
    args = parser.parse_args()
    output = ROOT / 'reports' / 'dynamic' / 'fifteen_policy_exhaustive_seed42'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url + '/dynamic/candidate-pool/')
        page.wait_for_selector('.candidate-pool-report')
        charts = page.locator('.candidate-pool-report .plot-distinct')
        assert charts.count() == 3
        for index, name in enumerate(('distribution', 'security', 'cost')):
            chart = charts.nth(index)
            boxes = chart.locator('input[type=checkbox]')
            lines = chart.locator(':scope > svg > .plot-series')
            assert boxes.count() == lines.count() == 10
            assert all(boxes.nth(i).is_checked() for i in range(10))
            for i in range(10):
                boxes.nth(i).uncheck()
            page.mouse.move(0, 0)
            assert lines.evaluate_all('(xs)=>xs.every(x=>getComputedStyle(x).display==="none")')
            boxes.nth(3).check()
            page.mouse.move(0, 0)
            assert lines.evaluate_all('(xs)=>xs.map((x,i)=>getComputedStyle(x).display!=="none"?i:null).filter(x=>x!==null)') == [3]
            for i in range(10):
                boxes.nth(i).check()
            page.mouse.move(0, 0)
            assert lines.evaluate_all('(xs)=>xs.every(x=>getComputedStyle(x).display!=="none"&&getComputedStyle(x).opacity==="1")')
            chart.locator('.series-key-2').hover()
            assert lines.evaluate_all('(xs)=>xs.map(x=>Number(getComputedStyle(x).opacity))') == [.08, .08, 1, .08, .08, .08, .08, .08, .08, .08]
            page.mouse.move(0, 0)
            boxes.nth(2).focus()
            page.keyboard.press('Tab')
            assert boxes.nth(3).evaluate('(x)=>x.matches(":focus-visible")')
            assert lines.evaluate_all('(xs)=>xs.map(x=>Number(getComputedStyle(x).opacity))') == [.08, .08, .08, 1, .08, .08, .08, .08, .08, .08]
            page.locator('h1').click()
            page.mouse.move(0, 0)
            chart.screenshot(path=str(output / f'top10_{name}_browser.png'))
            response = page.request.get(args.url + f'/dynamic/candidate-pool/top10_{name}.svg')
            assert response.status == 200
            ElementTree.fromstring(response.body())
        page.goto(args.url + '/dynamic')
        page.wait_for_selector('#candidate-pool .candidate-pool-report')
        assert page.locator('#candidate-pool .plot-distinct').count() == 3
        assert not errors, errors
        browser.close()
    print(json.dumps({'charts': 3, 'series_per_chart': 10, 'checkbox_selection': 'passed',
                      'hover_and_keyboard_focus': 'passed', 'svg_exports': 'passed',
                      'javascript_errors': errors}, ensure_ascii=False))


if __name__ == '__main__':
    main()
