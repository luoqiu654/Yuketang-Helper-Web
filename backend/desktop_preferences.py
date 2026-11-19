"""Small per-install desktop preferences store, mounted only by the desktop shell."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

_PATH = '/api/desktop/preferences'


class DesktopPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: Literal['zh', 'en']


def wrap_desktop_preferences(app, store: Path):
    """Route preferences separately so the web backend's SPA catch-all is unchanged.

    The desktop launcher MUST wrap the returned ASGI app in its session/host
    authentication layer before exposing it on the local listener.
    """
    store = Path(store)
    prefs_file = store / 'desktop-preferences.json'
    lock = threading.Lock()
    preferences = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @preferences.get(_PATH)
    def get_preferences():
        with lock:
            try:
                data = json.loads(prefs_file.read_text(encoding='utf-8'))
                language = data.get('language') if isinstance(data, dict) else None
            except (OSError, ValueError):
                language = None
        return {'language': language if language in ('zh', 'en') else None}

    @preferences.put(_PATH)
    def put_preferences(body: DesktopPreferences):
        with lock:
            store.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=store,
                                                 prefix='.desktop-preferences-', suffix='.tmp',
                                                 delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump({'language': body.language}, handle, ensure_ascii=False)
                os.replace(temporary, prefs_file)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return {'language': body.language}

    async def desktop_app(scope, receive, send):
        if scope['type'] == 'http' and scope.get('path') == _PATH:
            return await preferences(scope, receive, send)
        return await app(scope, receive, send)

    return desktop_app
