import json
import copy
import shutil
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import requests
from platformdirs import user_data_dir
from pydantic import BaseModel, Field, PositiveInt, ValidationError, field_validator


logger = logging.getLogger(__name__)


def _resolve_store_dir() -> Path:
    # 1. Explicit override — used by Docker (set in Dockerfile), CI, multi-instance.
    env_path = os.environ.get("YUKETANG_STORE_DIR")
    if env_path:
        return Path(env_path).expanduser()
    # 2. PyInstaller / frozen binary — OS-conventional per-user data dir.
    #    macOS:   ~/Library/Application Support/Yuketang Helper/
    #    Linux:   ~/.local/share/Yuketang Helper/
    #    Windows: %LOCALAPPDATA%\Yuketang Helper\
    if getattr(sys, "frozen", False):
        return Path(user_data_dir("Yuketang Helper", appauthor=False))
    # 3. Python source mode — keep store next to the repo for hackability.
    return Path(__file__).resolve().parent.parent / "store"


STORE_DIR = _resolve_store_dir()
STORE_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_PATH = STORE_DIR / "config.json"

DOMAIN_OPTIONS = [
    {"key": "www.yuketang.cn", "label": "Yuketang", "label_zh": "雨课堂"},
    {"key": "pro.yuketang.cn", "label": "Hetang Yuketang", "label_zh": "荷塘雨课堂"},
    {"key": "changjiang.yuketang.cn", "label": "Changjiang Yuketang", "label_zh": "长江雨课堂"},
    {"key": "huanghe.yuketang.cn", "label": "Huanghe Yuketang", "label_zh": "黄河雨课堂"},
]

DEFAULT_DOMAIN = "pro.yuketang.cn"
VALID_DOMAINS = {option["key"] for option in DOMAIN_OPTIONS}

# Per-account poll interval bounds. Lower bound prevents accidental DOS of
# Yuketang; upper bound stops typos turning the monitor into a no-op.
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 3600
DEFAULT_POLL_INTERVAL = 30

ChoiceAnswerMode = Literal["notify_only", "ai_suggest_confirm", "ai_auto"]
ShortAnswerMode = ChoiceAnswerMode
PushdeerLanguage = Literal["zh", "en"]
AIProviderName = Literal["google", "qwen"]


class DomainModel(BaseModel):
    domain: str = DEFAULT_DOMAIN

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, value: str) -> str:
        if value not in VALID_DOMAINS:
            raise ValueError("invalid domain")
        return value


class NotificationSubModel(BaseModel):
    enabled: bool = False
    signin: bool = True
    problem: bool = True
    call: bool = True
    danmu: bool = True
    red_packet: bool = True


class CourseSettingsModel(BaseModel):
    type1: ChoiceAnswerMode = "notify_only"
    type2: ChoiceAnswerMode = "notify_only"
    type3: ChoiceAnswerMode = "notify_only"
    type4: Literal["off"] = "off"
    type5: ShortAnswerMode = "notify_only"
    course_enabled: bool = True
    # Hold submissions until shortly before the deadline. Off by default; a
    # teacher closing a problem early would otherwise leave it unanswered.
    answer_near_deadline: bool = False
    auto_danmu: bool = True
    auto_redpacket: bool = True
    danmu_threshold: PositiveInt = 3
    notification: NotificationSubModel = Field(default_factory=NotificationSubModel)
    voice_notification: NotificationSubModel = Field(default_factory=NotificationSubModel)
    pushdeer_notification: NotificationSubModel = Field(default_factory=NotificationSubModel)
    windows_notification: NotificationSubModel = Field(default_factory=NotificationSubModel)


class AIKeyModel(BaseModel):
    name: str
    provider: AIProviderName
    key: str


class AIModel(BaseModel):
    keys: list[AIKeyModel] = Field(default_factory=list)
    active_key: int = -1
    fallback_keys: bool = True


class PushdeerKeyModel(BaseModel):
    name: str
    endpoint: str
    push_key: str


class PushdeerModel(BaseModel):
    keys: list[PushdeerKeyModel] = Field(default_factory=list)
    active_key: int = -1
    language: PushdeerLanguage = "zh"


class CourseListItemModel(BaseModel):
    classroom_id: str
    name: str
    classroom_name: str = ""
    teacher_name: str | None = None


class AccountModel(DomainModel):
    name: str = ""
    sessionid: str = ""
    user: dict = Field(default_factory=dict)
    course_list: list[CourseListItemModel] = Field(default_factory=list)
    courses: dict[str, CourseSettingsModel] = Field(default_factory=dict)
    ai: AIModel = Field(default_factory=AIModel)
    pushdeer: PushdeerModel = Field(default_factory=PushdeerModel)
    poll_interval: int = Field(default=DEFAULT_POLL_INTERVAL, ge=MIN_POLL_INTERVAL, le=MAX_POLL_INTERVAL)


class ConfigModel(BaseModel):
    schema_version: Literal[2] = 2
    active_account_id: str | None = None
    accounts: dict[str, AccountModel] = Field(default_factory=dict)


def get_poll_interval(account_id: str) -> int:
    acc = get_account(account_id)
    return acc["poll_interval"] if acc else DEFAULT_POLL_INTERVAL


def set_poll_interval(account_id: str, seconds: int) -> int:
    clamped = max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, seconds))
    _patch_account(account_id, poll_interval=clamped)
    return clamped


# ---------------------------------------------------------------------------
# Core load / save
# ---------------------------------------------------------------------------


# Serialize every read-modify-write transaction against store/config.json.
# Using RLock so helpers that call other locked helpers don't deadlock.
config_lock = threading.RLock()


def _protect_ai_keys(cfg: dict) -> dict:
    from credentials import store
    protected = copy.deepcopy(cfg)
    for account in protected.get("accounts", {}).values():
        for key in account.get("ai", {}).get("keys", []):
            secret = key.get("key", "")
            if secret and not secret.startswith("vault:"):
                key["key"] = store(_CONFIG_PATH.with_name("credentials.sqlite3"), secret)
    return protected


def _write_config(cfg: dict) -> None:
    # Atomic write: dump to *.tmp then os.replace so concurrent readers never
    # see a half-written file. Caller must hold config_lock.
    cfg = _protect_ai_keys(cfg)
    tmp_path = _CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _CONFIG_PATH)


def _backup_invalid_config() -> Path | None:
    """Rename the current config.json out of the way so we don't lose user data
    when the schema can't be parsed. Returns the backup path, or None on failure."""
    if not _CONFIG_PATH.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = _CONFIG_PATH.with_name(f"config.invalid-{stamp}.json")
    try:
        os.rename(_CONFIG_PATH, backup)
        return backup
    except OSError as e:
        logger.warning(f"Could not back up invalid config: {e}")
        return None


def _migrate_config(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Config must be an object")
    version = raw.get("schema_version", 1)
    if version == 2:
        return raw
    if version != 1:
        raise ValueError("Unsupported config schema version")
    migrated = copy.deepcopy(raw)
    mapping = {"ai": "ai_suggest_confirm", "off": "notify_only",
               "random": "notify_only", "blank": "notify_only"}
    for account in migrated.get("accounts", {}).values():
        for course in account.get("courses", {}).values():
            for key in ("type1", "type2", "type3", "type5"):
                if key in course:
                    if course[key] not in mapping:
                        raise ValueError("Unknown legacy answer mode")
                    course[key] = mapping[course[key]]
    migrated["schema_version"] = 2
    return migrated


def get_config() -> dict:
    with config_lock:
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            fresh = ConfigModel().model_dump()
            _write_config(fresh)
            return fresh
        # Fail closed: never reset or overwrite unparseable user data.
        migrated = _migrate_config(raw)
        validated = ConfigModel.model_validate(migrated).model_dump()
        needs_schema = raw.get("schema_version", 1) != 2
        needs_vault = any(key.get("key") and not key["key"].startswith("vault:")
                          for account in validated.get("accounts", {}).values()
                          for key in account.get("ai", {}).get("keys", []))
        if needs_schema or needs_vault:
            backup = _CONFIG_PATH.with_name("config.pre-v2.json" if needs_schema else "config.pre-vault.json")
            if backup.exists():
                raise RuntimeError("Migration backup already exists; resolve it before retrying")
            shutil.copy2(_CONFIG_PATH, backup)
            _write_config(validated)
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                validated = ConfigModel.model_validate(json.load(f)).model_dump()
        return validated


def save_config(cfg: dict) -> None:
    with config_lock:
        _write_config(ConfigModel.model_validate(cfg).model_dump())


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


def account_display_name(acc: dict, account_id: str) -> str:
    """Shared formula for "what to show as the human-readable account label"."""
    user = acc.get("user") or {}
    return user.get("name") or acc.get("name") or str(account_id)


def get_active_account_id() -> str | None:
    return get_config()["active_account_id"]


def set_active_account_id(account_id: str) -> None:
    with config_lock:
        cfg = get_config()
        cfg["active_account_id"] = account_id
        # cfg came from get_config() which already validated; skip re-validation.
        _write_config(cfg)


def list_accounts_summary() -> list[dict]:
    """UI-friendly summary: no secrets."""
    return [
        {
            "id": str(aid),
            "name": account_display_name(acc, aid),
            "avatar": (acc.get("user") or {}).get("avatar") or "",
            "domain": acc["domain"],
            "logged_in": bool(acc["sessionid"]),
        }
        for aid, acc in get_config()["accounts"].items()
    ]


def get_account(account_id: str) -> dict | None:
    return get_config()["accounts"].get(str(account_id))


def account_exists(account_id: str) -> bool:
    return str(account_id) in get_config()["accounts"]


def upsert_account(account_id: str, data: dict) -> None:
    with config_lock:
        cfg = get_config()
        # Only the new account dict is untrusted input — validate just it.
        cfg["accounts"][str(account_id)] = AccountModel.model_validate(data).model_dump()
        _write_config(cfg)


def _patch_account(account_id: str, *, subkey: str | None = None, **patch: Any) -> None:
    """Shared update helper for account, account.ai and account.pushdeer.

    Caller decides whether to use `subkey`:
      _patch_account(id, sessionid="...")              → mutates acc directly
      _patch_account(id, subkey="ai", active_key=1)    → mutates acc["ai"]
      _patch_account(id, subkey="pushdeer", ...)       → mutates acc["pushdeer"]

    A missing account is left alone: routes check existence first, so it can
    only have been deleted in between, and patching would bring it back empty.
    """
    with config_lock:
        cfg = get_config()
        accounts = cfg["accounts"]
        sid = str(account_id)
        if sid not in accounts:
            return
        target = accounts[sid][subkey] if subkey else accounts[sid]
        target.update(patch)
        # Validate just the touched account so we still catch corrupt patches,
        # without paying the cost of validating every other account on disk.
        accounts[sid] = AccountModel.model_validate(accounts[sid]).model_dump()
        _write_config(cfg)


def update_account(account_id: str, patch: dict) -> None:
    _patch_account(account_id, **patch)


def delete_account(account_id: str) -> None:
    with config_lock:
        cfg = get_config()
        cfg["accounts"].pop(str(account_id), None)
        if cfg["active_account_id"] == str(account_id):
            cfg["active_account_id"] = next(iter(cfg["accounts"].keys()), None)
        _write_config(cfg)


# ---------------------------------------------------------------------------
# Per-account getters (require account_id)
# ---------------------------------------------------------------------------


def get_domain(account_id: str) -> str:
    acc = get_account(account_id)
    return acc["domain"] if acc else DEFAULT_DOMAIN


def get_sessionid(account_id: str) -> str:
    acc = get_account(account_id)
    return acc["sessionid"] if acc else ""


def get_course_config(account_id: str, course_id: str) -> dict:
    acc = get_account(account_id)
    course = acc["courses"].get(str(course_id)) if acc else None
    return course or CourseSettingsModel().model_dump()


def update_course_config(account_id: str, course_id: str, data: dict) -> None:
    with config_lock:
        cfg = get_config()
        acc = cfg["accounts"].get(str(account_id))
        if acc is None:
            return
        acc["courses"][str(course_id)] = CourseSettingsModel.model_validate(data).model_dump()
        _write_config(cfg)


def get_ai_config(account_id: str) -> dict:
    acc = get_account(account_id)
    result = copy.deepcopy(acc["ai"]) if acc else AIModel().model_dump()
    from credentials import load
    for key in result["keys"]:
        if key["key"].startswith("vault:"):
            key["key"] = load(_CONFIG_PATH.with_name("credentials.sqlite3"), key["key"])
    return result


def update_ai_config(account_id: str, data: dict) -> None:
    _patch_account(account_id, subkey="ai", **data)


def get_pushdeer_config(account_id: str) -> dict:
    acc = get_account(account_id)
    return acc["pushdeer"] if acc else PushdeerModel().model_dump()


def update_pushdeer_config(account_id: str, data: dict) -> None:
    _patch_account(account_id, subkey="pushdeer", **data)


# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------


def make_headers(domain: str, sessionid: str) -> dict:
    return {
        "Cookie": f"sessionid={sessionid}",
        "Referer": f"https://{domain}/",
        "xt-agent": "web",
    }


def api_url(domain: str, template: str, **kwargs: Any) -> str:
    return template.format(domain=domain, **kwargs)


def ws_direct(domain: str) -> dict:
    """run_forever() options that keep a Yuketang WebSocket off any proxy, for
    the reason given in http_request. websocket-client honours `http_no_proxy`
    only next to an explicit proxy host, so this names a placeholder host (one
    that can never resolve) for the no-proxy entry to overrule."""
    return {
        "http_proxy_host": "direct.invalid",
        "http_proxy_port": 1,
        "proxy_type": "http",
        "http_no_proxy": [domain],
    }


_http_log = logging.getLogger(f"{__name__}.http")

# Yuketang serves a desktop layout when the UA looks like a desktop browser.
# The exact version doesn't matter; keep this fixed so traffic patterns don't
# shift just because a dependency was bumped.
_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/120.0"


def http_request(
    method: str,
    url: str,
    attempts: int = 10,
    timeout: int = 5,
    **kwargs: Any,
) -> requests.Response:
    """Issue an HTTP request with bounded retry. ``attempts`` is the *total*
    number of tries (so attempts=1 means no retry). 2xx/3xx/4xx return
    immediately; 5xx and connection errors retry with a 1–3s backoff."""
    kwargs.setdefault("timeout", timeout)
    headers = kwargs.setdefault("headers", {})
    headers.setdefault("User-Agent", _DEFAULT_UA)
    # Yuketang is always reached directly, whatever proxy the environment or
    # the system has configured: sign-in and answering are deadline-bound, and
    # a proxied round-trip is the difference between a late answer and none.
    # Every other host (PushDeer, slide images) follows the user's proxy
    # settings. The lesson and login WebSockets opt out with ws_direct().
    use_proxy_settings = urlsplit(url).hostname not in VALID_DOMAINS

    last_exc: Exception | None = None
    last_response: requests.Response | None = None
    for attempt in range(1, attempts + 1):
        try:
            with requests.Session() as session:
                session.trust_env = use_proxy_settings
                r = session.request(method, url, **kwargs)
            if r.status_code < 500:
                return r
            last_response = r
            _http_log.warning(f"HTTP {method} {url} → {r.status_code} (attempt {attempt}/{attempts})")
        except requests.RequestException as e:
            _http_log.warning(f"HTTP {method} {url} failed: {e} (attempt {attempt}/{attempts})")
            last_exc = e
        if attempt < attempts:
            time.sleep(min(attempt, 3))

    if last_exc is not None:
        raise last_exc
    # All attempts returned 5xx — surface that to the caller instead of
    # handing them a 5xx response they'll probably mis-parse as JSON.
    assert last_response is not None  # attempts >= 1 guarantees one branch ran
    last_response.raise_for_status()
    return last_response
