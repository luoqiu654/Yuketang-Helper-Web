from fastapi import APIRouter
from routers._common import AccountDep
import windows_notifications

router = APIRouter()

@router.post('/api/accounts/{account_id}/windows-notifications/test')
def test_windows_notification(account_id: str = AccountDep):
    # Trigger only when a user explicitly presses Test; no external service.
    # Windows can accept the request yet suppress the visual notification.
    ok = windows_notifications._show('雨课堂助手', '这是一条 Windows 通知测试。')
    return {'ok': ok, 'message': 'request_sent' if ok else 'notification_unavailable', **windows_notifications.diagnostics()}
