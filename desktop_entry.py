"""Windows desktop entry point. The original browser entry remains unchanged."""
from __future__ import annotations

import ctypes
import hashlib
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path


TITLE = '雨课堂助手 Evan V1.21'


def _message(text: str, error: bool = True) -> None:
    if sys.platform == 'win32':
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 if error else 0x40)
    else:
        print(text, file=sys.stderr)


class SingleInstance:
    """A session-local mutex and activation event scoped to the data directory."""
    def __init__(self, store: Path):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateEventW.restype = wintypes.HANDLE
        kernel.SetEvent.argtypes = [wintypes.HANDLE]
        kernel.SetEvent.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        name = hashlib.sha256(str(store.resolve()).casefold().encode('utf-8')).hexdigest()[:20]
        self.kernel = kernel
        ctypes.set_last_error(0)
        self.mutex = kernel.CreateMutexW(None, False, 'Local\\YuketangEvanDesktop-' + name)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == 183
        self.event = kernel.CreateEventW(None, False, False, 'Local\\YuketangEvanDesktopActivate-' + name)
        if not self.event:
            kernel.CloseHandle(self.mutex)
            raise ctypes.WinError(ctypes.get_last_error())
        self.stopping = threading.Event()

    def activate(self) -> None:
        self.kernel.SetEvent(self.event)

    def listen(self, show) -> None:
        while not self.stopping.is_set():
            if self.kernel.WaitForSingleObject(self.event, 500) == 0 and not self.stopping.is_set():
                try:
                    show()
                except Exception:
                    pass

    def close(self) -> None:
        self.stopping.set()
        self.kernel.SetEvent(self.event)
        self.kernel.CloseHandle(self.event)
        self.kernel.CloseHandle(self.mutex)


def _mount_source_frontend(app) -> None:
    """In source mode serve the production build using the same SPA routes."""
    if getattr(sys, 'frozen', False):
        return
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    root = Path(__file__).resolve().parent / 'frontend' / 'dist'
    if not (root / 'index.html').is_file() or not (root / 'assets').is_dir():
        raise RuntimeError('找不到前端构建文件。请先构建 frontend，再启动桌面版。')
    root = root.resolve()
    app.mount('/assets', StaticFiles(directory=root / 'assets'), name='desktop-assets')

    @app.get('/{full_path:path}')
    async def desktop_spa(full_path: str):
        if full_path.split('/', 1)[0] in ('api', 'ws'):
            raise HTTPException(404)
        candidate = (root / full_path).resolve()
        if candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        if '.' in candidate.name:
            raise HTTPException(404)
        return FileResponse(root / 'index.html')


def _run_tray_detached(icon, setup) -> None:
    """Run pystray's Windows loop without non-daemon startup threads.

    pystray 0.19.5 creates a non-daemon setup thread that blocks forever if
    the Windows tray loop fails before signaling readiness.  Mirror its small
    Windows setup sequence with daemon threads so that a broken tray cannot
    prevent the application from exiting.  The desktop requirements pin that
    pystray version; re-check these internals before upgrading it.
    """
    def setup_worker():
        icon._Icon__queue.get()
        setup(icon)

    icon._setup_thread = threading.Thread(target=setup_worker, name='tray-setup', daemon=True)
    icon._setup_thread.start()
    threading.Thread(target=icon._run, name='tray-event-loop', daemon=True).start()


class TrayController:
    def __init__(self, window):
        import pystray
        from PIL import Image, ImageDraw
        self.window = window
        self.exiting = False
        self.ready = threading.Event()
        icon_image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(icon_image)
        draw.rounded_rectangle((3, 3, 61, 61), radius=15, fill='#557d74')
        draw.rounded_rectangle((16, 15, 48, 49), radius=5, fill='#f7f4eb')
        for y in (23, 30, 37):
            draw.line((23, y, 41, y), fill='#557d74', width=3)
        self.icon = pystray.Icon('YuketangEvanDesktop', icon_image, TITLE, menu=pystray.Menu(
            pystray.MenuItem('打开窗口', self._show, default=True),
            pystray.MenuItem('退出并停止监听', self._exit),
        ))

    def start(self) -> bool:
        def setup(icon):
            icon.visible = True
            self.ready.set()
        _run_tray_detached(self.icon, setup)
        return self.ready.wait(5)

    def _show(self, *_args) -> None:
        self.window.show()
        self.window.restore()

    def _exit(self, *_args) -> None:
        self.exiting = True
        self.window.destroy()

    def closing(self):
        if self.exiting:
            return True
        if not self.ready.is_set():
            return True
        self.window.hide()
        return False

    def stop(self) -> None:
        self.icon.stop()


def run_desktop() -> int:
    if sys.platform != 'win32':
        _message('桌面客户端目前仅支持 Windows。')
        return 1
    from platformdirs import user_data_dir
    store = Path(os.environ.get('YUKETANG_STORE_DIR') or user_data_dir('Yuketang Evan Desktop', appauthor=False))
    os.environ['YUKETANG_STORE_DIR'] = str(store)
    store.mkdir(parents=True, exist_ok=True)
    guard = SingleInstance(store)
    if guard.already_running:
        guard.activate()
        guard.close()
        return 0

    # Windowed frozen applications can have no stderr; backend logging uses it.
    log_dir = store / 'logs'
    log_dir.mkdir(exist_ok=True)
    log_stream = None
    if sys.stderr is None or sys.stdout is None:
        log_stream = (log_dir / 'desktop-startup.log').open('a', encoding='utf-8')
        if sys.stderr is None:
            sys.stderr = log_stream
        if sys.stdout is None:
            sys.stdout = log_stream

    server = None
    server_thread = None
    listener_thread = None
    tray = None
    bound_socket = None
    stage = 'dependencies'

    def record_issue(label: str, exc: Exception) -> None:
        # Record only a stage and exception type; exception text can contain URLs.
        try:
            with (log_dir / 'desktop-startup.log').open('a', encoding='utf-8') as log:
                log.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {label}: {type(exc).__name__}\n')
        except OSError:
            pass

    try:
        if getattr(sys, 'frozen', False):
            import certifi
            os.environ['SSL_CERT_FILE'] = certifi.where()
            os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
        import uvicorn
        import webview
        from main import app
        from desktop_preferences import wrap_desktop_preferences
        from desktop_security import wrap_desktop_app

        stage = 'frontend'
        _mount_source_frontend(app)
        stage = 'socket'
        bound_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bound_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        bound_socket.bind(('127.0.0.1', 0))
        bound_socket.listen(128)
        port = bound_socket.getsockname()[1]
        token = secrets.token_urlsafe(32)
        protected = wrap_desktop_app(wrap_desktop_preferences(app, store), port, token)
        config = uvicorn.Config(protected, host='127.0.0.1', port=port, access_log=False,
                                log_level='info', log_config=None, timeout_graceful_shutdown=5)
        server = uvicorn.Server(config)
        server_thread = threading.Thread(target=server.run, kwargs={'sockets': [bound_socket]},
                                         name='desktop-server', daemon=True)
        stage = 'backend-start'
        server_thread.start()
        deadline = time.monotonic() + 15
        while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        if not server.started:
            raise RuntimeError('本地服务启动失败，请查看应用日志。')
        stage = 'window-create'
        window = webview.create_window(TITLE, f'http://127.0.0.1:{port}/__desktop__/bootstrap/{token}',
                                       width=1180, height=800, min_size=(840, 600), text_select=True)
        del token
        try:
            tray = TrayController(window)
            if tray.start():
                window.events.closing += tray.closing
            else:
                tray.stop()
                tray = None
        except Exception as exc:
            # Failure to create a tray must not make the window impossible to exit.
            record_issue('tray', exc)
            tray = None
        def show_window():
            window.show()
            window.restore()
        listener_thread = threading.Thread(target=guard.listen, args=(show_window,),
                                           name='desktop-activation', daemon=True)
        listener_thread.start()
        stage = 'webview-start'
        webview.start(gui='edgechromium', debug=False, private_mode=True)
        return 0
    except Exception as exc:
        # Do not place the bootstrap URL or credentials in error messages/logs.
        record_issue(stage, exc)
        _message('桌面客户端启动失败。请确认已安装 Microsoft Edge WebView2 Runtime，并查看应用数据目录的 logs。')
        return 1
    finally:
        if tray:
            try:
                tray.stop()
            except Exception:
                pass
        if server:
            server.should_exit = True
        if server_thread:
            server_thread.join(timeout=12)
        if bound_socket:
            try:
                bound_socket.close()
            except OSError:
                pass
        guard.stopping.set()
        guard.activate()
        if listener_thread:
            listener_thread.join(timeout=2)
        guard.close()
        if log_stream:
            log_stream.close()


if __name__ == '__main__':
    try:
        exit_code = run_desktop()
    except Exception:
        # Includes unwritable data directory and Windows mutex initialization.
        _message('桌面客户端无法启动，请检查应用数据目录是否可写。')
        exit_code = 1
    raise SystemExit(exit_code)
