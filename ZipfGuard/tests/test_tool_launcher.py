"""Regression checks for expired or reused local service PIDs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from ops import tool


@unittest.skipUnless(os.name == 'nt', 'Windows presentation launcher')
class LauncherProcessTests(unittest.TestCase):
    def test_stop_does_not_terminate_unrelated_reused_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / 'installation.json').write_text('{}', encoding='utf-8')
            record = runtime / 'processes.json'
            record.write_text(json.dumps([{'name': 'admin', 'pid': 424242}]), encoding='utf-8')
            unrelated = subprocess.CompletedProcess([], 0, stdout='python another_project/server.py', stderr='')
            with patch.object(tool.subprocess, 'run', return_value=unrelated), patch.object(tool.os, 'kill') as kill:
                tool.run('stop', runtime)
                kill.assert_not_called()
            self.assertFalse(record.exists())

    def test_start_preserves_running_service_record(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / 'installation.json').write_text('{}', encoding='utf-8')
            record = runtime / 'processes.json'
            record.write_text(json.dumps([{'name': 'admin', 'pid': 424242}]), encoding='utf-8')
            original = record.read_bytes()
            command = f'python "{tool.ROOT / "services/run.py"}" --kind admin --config "{runtime / "admin/config.json"}"'
            running = subprocess.CompletedProcess([], 0, stdout=command, stderr='')
            with patch.object(tool.subprocess, 'run', return_value=running), patch.object(tool.subprocess, 'Popen') as spawn:
                tool.run('start', runtime)
                spawn.assert_not_called()
            self.assertEqual(record.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
