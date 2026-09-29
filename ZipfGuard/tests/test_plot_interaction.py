"""Browser regression checks for independent selection and transient emphasis."""
import unittest
from pathlib import Path

from web.presentation import STYLE, plot

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'Install requirements-ui-test.txt for browser checks')
class PlotInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        edge = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        cls.browser = cls.runtime.chromium.launch(
            headless=True, executable_path=str(edge) if edge.exists() else None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1280, 'height': 1400})
        series = [(name, [(100, .01 * (i + 1)), (1000, .1 * (i + 1))])
                  for i, name in enumerate(['A', 'B', 'C'])]
        chart = plot(series, xlabel='累计猜测次数', ylabel='累计猜中的用户比例',
                     log=True, distinguish=True, x_ticks=[100, 1000],
                     x_format='count', y_format='percent')
        self.page.set_content(f'<style>{STYLE}</style><button id="start">开始</button>' + chart + chart)
        self.chart = self.page.locator('.plot-distinct').first
        self.boxes = self.chart.locator('input[type=checkbox]')

    def tearDown(self):
        self.page.close()

    def states(self, chart=None):
        return (chart or self.chart).locator('svg > .plot-series').evaluate_all(
            "nodes => nodes.map(n => ({visible: getComputedStyle(n).display !== 'none',"
            "opacity: Number(getComputedStyle(n).opacity)}))")

    def test_checkboxes_only_show_their_own_series(self):
        self.assertTrue(all(s['visible'] and s['opacity'] == 1 for s in self.states()))
        for box in self.boxes.all():
            box.uncheck()
        self.page.mouse.move(0, 0)
        self.assertEqual([s['visible'] for s in self.states()], [False] * 3)
        for i in range(3):
            self.boxes.nth(i).check()
            self.page.mouse.move(0, 0)
            self.assertEqual([s['visible'] for s in self.states()], [k == i for k in range(3)])
            self.assertEqual(self.states()[i]['opacity'], 1)
            self.boxes.nth(i).uncheck()
        for box in self.boxes.all():
            box.check()
        self.page.mouse.move(0, 0)
        self.assertTrue(all(s['visible'] and s['opacity'] == 1 for s in self.states()))
        other = self.page.locator('.plot-distinct').nth(1)
        self.assertTrue(all(s['visible'] and s['opacity'] == 1 for s in self.states(other)))

    def test_hover_wins_over_keyboard_focus_and_does_not_reveal_hidden_series(self):
        self.page.locator('#start').click()
        self.page.keyboard.press('Tab')
        self.assertEqual(self.boxes.nth(0).evaluate('(n) => n === document.activeElement'), True)
        self.assertEqual([s['opacity'] for s in self.states()], [1, .08, .08])
        self.chart.locator('.series-key-1').hover()
        self.assertEqual([s['opacity'] for s in self.states()], [.08, 1, .08])
        self.page.mouse.move(0, 0)
        self.assertEqual([s['opacity'] for s in self.states()], [1, .08, .08])
        self.boxes.nth(1).uncheck()
        self.chart.locator('.series-key-1').hover()
        self.assertEqual([s['visible'] for s in self.states()], [True, False, True])
        self.assertEqual([s['opacity'] for s in self.states()], [1, 1, 1])

    def test_measured_budget_ticks_and_percent_tooltips(self):
        self.assertEqual(self.chart.locator('.x-tick').all_text_contents(), ['100', '1,000'])
        titles = self.chart.locator('.plot-series title').all_text_contents()
        self.assertIn('A；累计猜测次数：1,000；累计猜中的用户比例：10.00%', titles)


if __name__ == '__main__':
    unittest.main()
