"""Continuation delivery must survive the issue PTY's process-group shutdown."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

RUNNER = Path(__file__).resolve().parent.parent / "bin" / "claude-select-run"

WORKER = r"""
import importlib.machinery
import importlib.util
from contextlib import nullcontext
import os
import signal
import time
from types import SimpleNamespace
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('runner', os.environ['RUNNER'])
spec = importlib.util.spec_from_loader('runner', loader)
runner = importlib.util.module_from_spec(spec)
loader.exec_module(runner)
# Like the real runner, stay alive when Divernote stops the issue group.
signal.signal(signal.SIGTERM, lambda *_: None)
state = {'accounts': {'one': {}, 'two': {}}}
selector = SimpleNamespace(accounts=[('one', '/unused'), ('two', '/unused2')],
    state_lock=nullcontext, load_state=lambda: state, save_state=lambda _: None)
with patch.object(runner, 'eligible', return_value=True), \
     patch.object(runner, 'logged_in', return_value=True), \
     patch.object(runner.shutil, 'which', return_value=os.environ['HELPER']):
    runner.on_limit(selector, 'one', (time.time() + 60, 'usage limit', True), 'test-session', True)
"""

HELPER = r"""
import json
import os
from pathlib import Path
import sys
import time

Path(os.environ['READY']).write_text(str(os.getpid()))
time.sleep(0.5)
mode = os.environ.get('HELPER_MODE', 'success')
if mode == 'failure':
    print('vault unavailable', file=sys.stderr)
    sys.exit(23)
if mode != 'empty':
    Path(os.environ['SAVED']).write_text('queued')
    print(json.dumps({'status': 'later'}))
"""


class Continuation(unittest.TestCase):
    def run_delivery(self, *, shutdown=False, mode='success'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'divernote'
            helper.write_text(f'#!{sys.executable}\n' + HELPER)
            helper.chmod(0o700)
            env = {**os.environ, 'RUNNER': str(RUNNER), 'HELPER': str(helper),
                   'READY': str(root / 'ready'), 'SAVED': str(root / 'saved'),
                   'HELPER_MODE': mode, 'DIVERNOTE_CLAUDE_SELECT_LOG': str(root / 'rotate.log')}
            worker = subprocess.Popen([sys.executable, '-c', WORKER], env=env,
                                      start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 10
                while not (root / 'ready').exists():
                    if worker.poll() is not None or time.monotonic() > deadline:
                        self.fail('continuation helper never started')
                    time.sleep(0.01)
                if shutdown:
                    os.killpg(worker.pid, signal.SIGTERM)
                stdout, stderr = worker.communicate(timeout=10)
                self.assertEqual(worker.returncode, 0, (stdout, stderr))
                return (root / 'saved').exists(), (root / 'rotate.log').read_text()
            finally:
                if worker.poll() is None:
                    os.killpg(worker.pid, signal.SIGKILL)
                    worker.communicate()

    def test_continuation_survives_issue_group_shutdown(self):
        saved, log = self.run_delivery(shutdown=True)
        self.assertTrue(saved, log)
        self.assertIn('continue queued for test-session', log)

    def test_failed_delivery_is_not_logged_as_queued(self):
        saved, log = self.run_delivery(mode='failure')
        self.assertFalse(saved)
        self.assertNotIn('continue queued', log)
        self.assertIn('could not queue continue', log)
        self.assertIn('23', log)
        self.assertIn('vault unavailable', log)

    def test_empty_success_is_not_logged_as_queued(self):
        saved, log = self.run_delivery(mode='empty')
        self.assertFalse(saved)
        self.assertNotIn('continue queued', log)
        self.assertIn('could not confirm continue', log)


if __name__ == '__main__':
    unittest.main()
