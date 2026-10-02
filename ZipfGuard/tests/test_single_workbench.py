import threading
import gzip
import json
import tempfile
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from core.distributions import analyze_counts
from web.server import Handler
from web.intervention_interface import intervention_index_with_latest, latest_intervention_result


class SingleWorkbenchTests(unittest.TestCase):
    def test_latest_report_is_present_in_initial_workbench_html(self):
        output = {'run_id': '0123456789abcdef', 'html': '<main id="risk_cost">new chart</main>'}
        with patch('web.intervention_interface.latest_intervention_result',
                   return_value={'output': output}):
            html = intervention_index_with_latest()
        self.assertIn('data-run-id="0123456789abcdef"', html)
        self.assertIn('<main id="risk_cost">new chart</main>', html)

    def test_published_snapshot_loads_when_local_reports_are_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = '0123456789abcdef'
            published = root/'published'
            (published/run_id).mkdir(parents=True)
            (published/'latest.json').write_text(json.dumps({'run_id': run_id}), encoding='utf-8')
            with gzip.open(published/run_id/'report.json.gz', 'wt', encoding='utf-8') as handle:
                json.dump({'schema_version': 'selective-intervention-v1-result',
                           'metadata': {'run_id': run_id}}, handle)
            with patch('web.intervention_interface.REPORT_ROOT', root/'empty'), patch(
                    'web.intervention_interface.PUBLISHED_ROOT', published), patch(
                    'web.intervention_interface.published',
                    side_effect=lambda row: {'run_id': row['metadata']['run_id'], 'html': 'chart'}):
                self.assertEqual(latest_intervention_result()['output']['run_id'], run_id)

    def test_default_fitter_never_invokes_legacy_model_selection(self):
        with patch('core.distributions.fit_model', side_effect=AssertionError('legacy fit called')):
            result = analyze_counts([90, 50, 20, 10]+[1]*100, bootstrap_repetitions=20, seed=1)
        self.assertEqual(result['selected_model'], 'cdf_sampling')
        self.assertEqual(len(result['models']), 1)
        self.assertIsNone(result['models'][0]['bic'])

    def test_only_intervention_web_surface_and_old_address_redirects(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = HTTPConnection('127.0.0.1', server.server_port)
        try:
            for route in ('/', '/intervention'):
                conn.request('GET', route)
                response = conn.getresponse()
                body = response.read().decode('utf-8')
                self.assertEqual(response.status, 200)
                self.assertIn('第三展示台', body)
                self.assertIn('CDF 采样', body)
                self.assertNotIn('第一展示台', body)
                self.assertNotIn('第二展示台', body)
                self.assertNotIn('href="/open"', body)
                self.assertNotIn('href="/dynamic"', body)
            for route in ('/open', '/dynamic', '/legacy', '/dynamic/candidate-pool/'):
                conn.request('GET', route)
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 302)
                self.assertEqual(response.getheader('Location'), '/intervention')
            for method, route in (('GET','/api/dynamic/latest'), ('GET','/api/demo'),
                                  ('POST','/api/dynamic/jobs'), ('POST','/api/run')):
                conn.request(method, route)
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 404)
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
