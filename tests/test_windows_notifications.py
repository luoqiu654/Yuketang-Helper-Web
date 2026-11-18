import sys, os, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

_tmp = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = _tmp.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import windows_notifications as notify
from config import CourseSettingsModel

class TestWindowsNotifications(unittest.TestCase):
    def setUp(self): notify._seen.clear()

    def test_default_is_off_and_old_course_migrates(self):
        self.assertFalse(CourseSettingsModel.model_validate({}).windows_notification.enabled)
        self.assertFalse(CourseSettingsModel.model_validate({'course_enabled': True}).windows_notification.enabled)

    def test_opt_in_and_selected_event(self):
        with patch.object(notify.sys, 'platform', 'win32'), \
             patch.object(notify, 'get_course_config', return_value={'windows_notification': {'enabled': True, 'problem': True, 'call': False}}), \
             patch.object(notify.threading, 'Thread') as thread:
            self.assertTrue(notify.dispatch('a', 'c', 'problem_received', {'lessonid': 1, 'problemid': 2}))
            self.assertFalse(notify.dispatch('a', 'c', 'problem_received', {'lessonid': 1, 'problemid': 2}))
            self.assertFalse(notify.dispatch('a', 'c', 'call', {'lessonid': 1}))
            thread.assert_called_once()
            # The mocked worker does not release its acquired slot.
            notify._slots.release()

    def test_course_disabled_and_other_platform(self):
        with patch.object(notify.sys, 'platform', 'win32'), patch.object(notify, 'get_course_config', return_value={'windows_notification': {'enabled': False}}), patch.object(notify.threading, 'Thread') as thread:
            self.assertFalse(notify.dispatch('a','c','problem_received',{'problemid': 1}))
            thread.assert_not_called()
        with patch.object(notify.sys, 'platform', 'linux'):
            self.assertFalse(notify.dispatch('a','c','problem_received',{'problemid': 1}))

    def test_safe_process_invocation(self):
        secret_marker='example-credential-never-in-command'
        with patch.object(notify.sys, 'platform', 'win32'), patch.object(notify.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(notify._show('test', secret_marker))
            self.assertNotIn(secret_marker, str(run.call_args.args[0]))
            self.assertIn(secret_marker, run.call_args.kwargs['input'])
        with patch.object(notify.sys, 'platform', 'win32'), patch.object(notify.subprocess, 'run', return_value=SimpleNamespace(returncode=3)):
            self.assertFalse(notify._show('test','test'))

if __name__=='__main__': unittest.main()
