"""Windows toast notifications, dispatched by the backend independently of the UI.

PowerShell's existing Start-menu app identity is used so no system registration
or package installation is required. Windows may suppress notifications due to
its notification settings or Focus Assist. Payloads contain only a fixed event
label and optional lesson name; credentials and event details are never passed.
"""
import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

from config import get_course_config

logger = logging.getLogger(__name__)
_EVENT_NAMES = {
    'signin': ('signin', '课堂签到状态更新'),
    'problem_received': ('problem', '收到课堂题目，请及时查看'),
    'problem': ('problem', 'AI 建议失败，请在雨课堂手动作答'),
    'call': ('call', '课堂点名提醒'),
    'danmu': ('danmu', '课堂弹幕状态更新'),
    'red_packet': ('red_packet', '课堂红包状态更新'),
}
_DEDUP_SECONDS = 300
_seen = OrderedDict()
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(8)

# Static script only: untrusted data is sent through stdin and inserted into XML
# with InnerText, never interpolated into PowerShell or put on the command line.
_TOAST_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).InnerText = $data.title
$nodes.Item(1).InnerText = $data.body
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
if ($null -ne $notifier.Setting -and $notifier.Setting.ToString() -ne 'Enabled') { exit 3 }
$notifier.Show($toast)
'''
_ENCODED_SCRIPT = base64.b64encode(_TOAST_SCRIPT.encode('utf-16-le')).decode('ascii')


def diagnostics() -> dict:
    """Read desktop-session notification state without changing system settings.

    This legacy Windows API reports whether interruption is appropriate; it does
    not prove that banners, Do Not Disturb, or per-app settings allow display.
    """
    result = {'platform_supported': sys.platform == 'win32', 'source': 'Windows PowerShell', 'session_state': 'unknown'}
    if sys.platform != 'win32':
        return result
    try:
        import ctypes
        state = ctypes.c_int()
        query = ctypes.windll.shell32.SHQueryUserNotificationState
        query.argtypes = [ctypes.POINTER(ctypes.c_int)]
        query.restype = ctypes.c_long
        if query(ctypes.byref(state)) == 0:
            result['session_state'] = {
                1: 'not_present', 2: 'busy', 3: 'fullscreen',
                4: 'presentation', 5: 'accepts_notifications',
                6: 'quiet_time', 7: 'windows_app',
            }.get(state.value, 'unknown')
    except (AttributeError, OSError):
        pass
    return result


def _show(title: str, body: str) -> bool:
    """Request a Windows toast; success indicates invocation, not user delivery."""
    if sys.platform != 'win32':
        return False
    exe = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    try:
        result = subprocess.run(
            [str(exe), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', _ENCODED_SCRIPT],
            input=json.dumps({'title': title, 'body': body}, ensure_ascii=True), text=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False,
        )
        if result.returncode != 0:
            logger.warning('Windows notification unavailable or disabled (code %s)', result.returncode)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        logger.warning('Windows notification could not be displayed')
        return False


def _run(title: str, body: str) -> None:
    try:
        _show(title, body)
    finally:
        _slots.release()


def dispatch(account_id: str, classroom_id: str, event_type: str, data: dict) -> bool:
    """Schedule an enabled course event, returning whether it was queued."""
    entry = _EVENT_NAMES.get(event_type)
    if event_type == 'problem' and data.get('status') != 'ai_failed':
        return False
    if sys.platform != 'win32' or not entry:
        return False
    key, text = entry
    options = get_course_config(account_id, classroom_id).get('windows_notification', {})
    if not options.get('enabled', False) or not options.get(key, True):
        return False
    identity = None
    problem_id = data.get('problemid')
    if event_type == 'problem_received' and problem_id is not None:
        identity = (str(account_id), str(data.get('lessonid')), str(problem_id))
    if not _slots.acquire(blocking=False):
        logger.warning('Windows notification queue is full')
        return False
    if identity is not None:
        now = time.monotonic()
        with _lock:
            while _seen and (now - next(iter(_seen.values())) > _DEDUP_SECONDS or len(_seen) >= 2048):
                _seen.popitem(last=False)
            if identity in _seen:
                _slots.release()
                return False
            _seen[identity] = now
    # Avoid passing classroom/user-controlled text or credentials to OS logs.
    try:
        threading.Thread(target=_run, args=('雨课堂助手', text), daemon=True, name='windows-toast').start()
    except Exception:
        if identity is not None:
            with _lock:
                _seen.pop(identity, None)
        _slots.release()
        raise
    return True
