"""Offline desktop tray lifecycle checks, without creating a real tray icon."""
import subprocess
import sys
import unittest
from pathlib import Path


class TrayStartupTests(unittest.TestCase):
    def test_tray_startup_failure_does_not_keep_process_alive(self):
        code = r'''
import queue
import time
from desktop_entry import _run_tray_detached

class FailedTray:
    def __init__(self):
        self._Icon__queue = queue.Queue()
    def _run(self):
        return  # fail before the tray can signal readiness

_run_tray_detached(FailedTray(), lambda icon: None)
time.sleep(0.05)
'''
        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_setup_runs_after_tray_is_ready(self):
        code = r'''
import queue
import threading
from desktop_entry import _run_tray_detached

class WorkingTray:
    def __init__(self):
        self._Icon__queue = queue.Queue()
    def _run(self):
        self._Icon__queue.put(True)

ready = threading.Event()
_run_tray_detached(WorkingTray(), lambda icon: ready.set())
assert ready.wait(1)
'''
        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
