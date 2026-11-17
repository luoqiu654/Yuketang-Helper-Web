import json
import logging
import random
import threading
import time
from datetime import datetime, timezone
from enum import Enum, auto
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

import websocket

from config import api_url, get_account, http_request, make_headers, ws_direct

logger = logging.getLogger(__name__)

# API URLs
URL_WSS = "wss://{domain}/wsapp/"
URL_CHECKIN = "https://{domain}/api/v3/lesson/checkin"
URL_DANMU_SEND = "https://{domain}/api/v3/lesson/danmu/send"
URL_PROBLEM_ANSWER = "https://{domain}/api/v3/lesson/problem/answer"
URL_PRESENTATION_FETCH = "https://{domain}/api/v3/lesson/presentation/fetch?presentation_id={presentation_id}"
URL_REDENVELOPE_PREPARE = "https://{domain}/api/v3/lesson/redenvelope/prepare"

# Answering is deadline-bound, so these calls trade retry depth for speed:
# a slow retry chain would push the submission past the teacher's cut-off.
_ANSWER_ATTEMPTS = 3
_ANSWER_TIMEOUT = 3
# Upper bound on how long we wait for an AI answer when the problem carries no
# deadline of its own. Without it a hung provider means no answer at all, and
# teachers often close untimed problems within a minute or two.
_AI_MAX_WAIT = 45.0
# "Answer near deadline" submits this many seconds before the deadline. The
# margin absorbs a slow or retried submit; 1-5s lost answers to one hiccup.
_DEADLINE_LEAD = (10.0, 15.0)
# Connections in a row that deliver no frame before we log a warning: the
# lesson token from check-in is reused on every reconnect, and if Yuketang
# stops accepting it we would reconnect forever without seeing a problem.
_SILENT_CONNECTIONS_WARN = 3
# Short-lived connections are failures even if they deliver a greeting frame.
# Normal server-initiated reconnects are usually much longer lived.
_STABLE_WS_SECONDS = 30.0
# Rejected submissions before we stop retrying a problem. Transient errors
# clear in a try or two; retrying forever would hammer Yuketang on every frame.
_MAX_SUBMIT_FAILS = 3


class AIKeyAttempt(NamedTuple):
    provider: str
    api_key: str
    name: str


class _Stop(Enum):
    """Lesson lifecycle state. NONE means the lesson is still running."""
    NONE = auto()
    EXTERNAL = auto()   # stop_lesson() called (Monitor evicted us)
    FINISHED = auto()   # `lessonfinished` WS frame received


class Lesson:
    def __init__(
        self,
        account_id: str,
        lesson_data: dict,
        sessionid: str,
        domain: str,
        course_config: dict,
        on_event: Callable[[str, dict], None],
    ):
        self.account_id = account_id
        self.lessonid: int = lesson_data["lessonid"]
        self.lessonname: str = lesson_data["lessonname"]
        self.classroomid: int = lesson_data["classroomid"]
        self.domain = domain
        self.course_config = course_config
        self.on_event = on_event

        self.headers = make_headers(domain, sessionid)
        self.auth: str | None = None
        self.wsapp: websocket.WebSocketApp | None = None
        self._stop_reason: _Stop = _Stop.NONE
        # Set together with _stop_reason: wakes answer threads that are holding
        # for their submission window so they submit now instead of expiring.
        self._stop_event = threading.Event()

        self.danmu_dict: dict[str, list[float]] = {}
        self.sent_danmu_dict: dict[str, float] = {}
        # Known problems, keyed by BOTH problemId and slide id: the WS frames
        # identify a problem by either one depending on the op, and Yuketang's
        # two id namespaces don't overlap.
        self._problems: dict[Any, dict] = {}
        # problemIds that are answered — by us, or already on the server.
        # Guards against double submission; a failed submit is removed again so
        # the next reconcile retries it.
        self._answered: set = set()
        # Attempts we've given up on, keyed by problemId — or by the raw id from
        # `unlockedproblem` when we couldn't even resolve which problem it is.
        self._failures: dict[Any, int] = {}
        self._answered_lock = threading.Lock()
        # problemIds we've told the user we won't answer, so each gets one notice.
        self._skip_notified: set = set()
        # Seconds to add to the local clock to get Yuketang's. Only ever moves
        # forward: a frame's `dt` is at or before the server's "now", and
        # underestimating the time left is the safe direction.
        self._server_offset = 0.0
        self.ws_health = {"state": "CONNECTING", "last_connected_at": None, "last_frame_at": None, "reconnect_count": 0, "consecutive_failures": 0}
        self._frames = 0  # frames received on the current connection

        self.user_uid: int | None = None
        self.user_uname: str | None = None

    def _is_running(self) -> bool:
        return self._stop_reason is _Stop.NONE

    def _stop(self, reason: _Stop) -> None:
        self._stop_reason = reason
        self._stop_event.set()

    def _hold(self, seconds: float) -> None:
        """Wait out a submission window, waking early if the lesson stops —
        the caller submits either way, so a lesson ending mid-hold costs us
        the delay, never the answer."""
        if seconds > 0:
            self._stop_event.wait(seconds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_lesson(self) -> None:
        # Raises if check-in yielded no lesson token; Monitor frees the slot on
        # the way out so the next poll retries instead of leaving the class
        # tracked but unattended.
        self._checkin()

        # Normal, long-lived server disconnects get a fast reconnect. Repeated
        # short connections back off even if each one delivered a hello frame.
        silent = 0
        failures = 0
        while self._is_running():
            self._frames = 0
            self.wsapp = websocket.WebSocketApp(
                url=api_url(self.domain, URL_WSS),
                header=self.headers,
                on_open=self._on_open,
                on_message=self._on_message,
            )
            started = time.monotonic()
            try:
                self.wsapp.run_forever(ping_interval=30, ping_timeout=10, **ws_direct(self.domain))
            except Exception:
                # Do not log socket exception text: it may contain credentials.
                self.ws_health.update(last_error_code="WS_ERROR", last_error_at=datetime.now(timezone.utc).isoformat())
                logger.warning("[%s] classroom WebSocket failed", self.account_id)
            if not self._is_running():
                break
            self.ws_health["state"] = "RECOVERING"
            self.ws_health["reconnect_count"] += 1
            silent = silent + 1 if self._frames == 0 else 0
            failures = failures + 1 if time.monotonic() - started < _STABLE_WS_SECONDS else 0
            self.ws_health["consecutive_failures"] = failures
            self.on_event("monitor_health_changed", {"classroom_health": {"lesson_id": self.lessonid, **self.ws_health}})
            if silent >= _SILENT_CONNECTIONS_WARN:
                logger.warning(
                    f"[{self.account_id}][WS {self.lessonname}] {silent} connections in a row delivered "
                    f"no frames; the lesson token may no longer be accepted"
                )
            delay = min(30.0, 2 ** min(max(failures - 1, 0), 5))
            delay *= random.uniform(0.8, 1.0)
            logger.info(f"[{self.account_id}][WS {self.lessonname}] disconnected, retrying in {delay:.1f}s")
            self._stop_event.wait(delay)

        # Monitor._sync_lessons already emits lesson_end on external stop,
        # so only emit it here when we exited because the lesson finished.
        if self._stop_reason is _Stop.FINISHED:
            self.on_event("lesson_end", {"lesson": self.lessonname, "lessonid": self.lessonid})

    def stop_lesson(self) -> None:
        self._stop(_Stop.EXTERNAL)
        if self.wsapp:
            self.wsapp.close()

    def send_danmu(self, content: str) -> None:
        payload = {
            "lessonId": self.lessonid,
            "target": "",
            "userName": "",
            "message": content,
            "extra": "",
            "requiredCensor": False,
            "wordCloud": True,
            "showStatus": True,
            "fromStart": "50",
        }
        # Runs on its own thread: a failure has to become an event, or it
        # would end in an uncaught traceback the user never sees.
        try:
            r = http_request("POST", api_url(self.domain, URL_DANMU_SEND), headers=self.headers, json=payload)
            result = r.json()
        except Exception as e:
            logger.warning(f"[{self.account_id}] danmu send failed: {e}")
            result = {"code": -1, "msg": str(e)}
        self.on_event("danmu", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "content": content,
            "status": "success" if result.get("code") == 0 else "error",
            "message": result.get("msg", ""),
        })

    def _grab_red_packet(self, red_envelope_id: int) -> None:
        payload = {
            "lessonId": self.lessonid,
            "redEnvelopeId": red_envelope_id,
        }
        try:
            r = http_request("POST", api_url(self.domain, URL_REDENVELOPE_PREPARE), headers=self.headers, json=payload)
            result = r.json()
        except Exception as e:
            logger.warning(f"[{self.account_id}] red packet {red_envelope_id} grab failed: {e}")
            result = {"code": -1, "msg": str(e)}
        self.on_event("red_packet", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "redEnvelopeId": red_envelope_id,
            "status": "success" if result.get("code") == 0 else "error",
            "message": result.get("msg", ""),
        })

    # ------------------------------------------------------------------
    # Check-in
    # ------------------------------------------------------------------

    def _checkin(self) -> None:
        """Sign in and pick up the lesson token the WS needs. Always emits a
        `signin` event, failures included, so a missed check-in reaches the
        user's notifications instead of dying in a log."""
        try:
            r = http_request("POST", api_url(self.domain, URL_CHECKIN), headers=self.headers,
                             json={"source": 21, "lessonId": self.lessonid})
            set_auth = r.headers.get("Set-Auth")
            if set_auth:
                self.headers["Authorization"] = f"Bearer {set_auth}"
            result = r.json()
            token = (result.get("data") or {}).get("lessonToken")
            message = result.get("msg", "")
        except Exception as e:
            logger.warning(f"[{self.account_id}] check-in request failed for lesson {self.lessonid}: {e}")
            token, message = None, str(e)

        self.on_event("signin", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "status": "success" if token else "error",
            "message": message,
        })
        if not token:
            raise RuntimeError(f"check-in failed: {message}")
        self.auth = token

        acc = get_account(self.account_id) or {}
        user = acc.get("user") or {}
        self.user_uid = user.get("id")
        self.user_uname = user.get("name")

    # ------------------------------------------------------------------
    # Problem bookkeeping
    # ------------------------------------------------------------------

    def _fetch_presentation(self, presentation_id: Any, attempts: int = _ANSWER_ATTEMPTS, timeout: int = 4) -> None:
        """(Re-)load a presentation's problems into `_problems`. Always re-reads:
        one cheap GET refreshes each problem's `result`, which is how we know a
        problem was answered elsewhere and must not be overwritten."""
        r = http_request("GET", api_url(self.domain, URL_PRESENTATION_FETCH, presentation_id=presentation_id),
                         headers=self.headers, attempts=attempts, timeout=timeout)
        for slide in r.json()["data"].get("slides", []):
            problem = slide.get("problem")
            if not problem:
                continue
            problem["_cover"] = slide.get("cover", "")
            problem["_pres"] = presentation_id
            self._problems[problem["problemId"]] = problem
            if slide.get("id"):
                self._problems[slide["id"]] = problem

    def _load_presentations(self, presentation_ids: Iterable) -> None:
        """Fetch each presentation independently so one failure doesn't hide
        the problems in the others."""
        for pid in presentation_ids:
            if not pid:
                continue
            try:
                self._fetch_presentation(pid)
            except Exception as e:
                logger.warning(f"[{self.account_id}] presentation {pid} fetch failed: {e}")

    def _refreshed(self, problem: dict, timeout: int) -> dict:
        """Re-read one problem from its presentation, or the cached copy if that
        fails. A single attempt: callers are racing a deadline."""
        try:
            self._fetch_presentation(problem["_pres"], attempts=1, timeout=timeout)
        except Exception as e:
            logger.warning(f"[{self.account_id}] presentation {problem['_pres']} refresh failed: {e}")
        return self._problems.get(problem["problemId"], problem)

    def _mode_for(self, problem: dict) -> str:
        return self.course_config.get(f"type{problem['problemType']}", "off")

    def _skip_reason(self, problem: dict) -> str | None:
        if problem["problemType"] not in (1, 2, 3, 5):
            return "unsupported"
        if self._mode_for(problem) == "off":
            return "off"
        return None

    def _notify_skipped(self, problem: dict, reason: str) -> None:
        """Tell the user, once per problem, that we won't answer it, so nothing
        sits unanswered without an explanation. `reason` is "unsupported",
        "off" (type turned off) or "answered" (answered by hand while we held)."""
        problemid = problem["problemId"]
        if problemid in self._skip_notified:
            return
        self._skip_notified.add(problemid)
        self.on_event("problem", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "problemid": problemid,
            "problemtype": problem["problemType"],
            "status": "skipped",
            "message": reason,
        })

    def _remaining_limit(self, problem: dict) -> int:
        """Seconds left on a problem's own clock, from the presentation data.
        0 means "no deadline known" — submit without holding."""
        limit = int(problem.get("limit") or 0)
        sent_ms = int(float(problem.get("sendTime") or 0))
        if limit <= 0 or sent_ms <= 0:
            return 0
        return max(0, int(sent_ms / 1000 + limit - (time.time() + self._server_offset)))

    def _claim(self, problemid: Any) -> bool:
        """Take ownership of answering a problem. False means it's already
        claimed, answered, or hopeless — the guard against double submission."""
        with self._answered_lock:
            if problemid in self._answered or self._failures.get(problemid, 0) >= _MAX_SUBMIT_FAILS:
                return False
            self._answered.add(problemid)
            return True

    def _answer_if_needed(self, problem: dict | None, limit: int, context: str) -> None:
        """The one way in to answering: both the live `unlockproblem` frame and
        the reconcile pass go through here, so the guards can't be bypassed."""
        if problem is None:
            logger.warning(f"[{self.account_id}] could not resolve problem ({context})")
            self.on_event("problem", {
                "lesson": self.lessonname,
                "lessonid": self.lessonid,
                "status": "error",
                "message": f"problem not found ({context})",
            })
            return
        problemid = problem["problemId"]
        if problem.get("result") is not None:
            # Answered already (by us on a previous run, or by hand elsewhere).
            from quiz.store import mark_answered
            mark_answered(self.account_id, self.lessonid, problemid)
            self._claim(problemid)
            return
        from quiz import store
        try:
            if store.create(self.account_id, self, problem):
                self.on_event("problem_received", {"lesson": self.lessonname,
                    "lessonid": self.lessonid, "problemid": problemid})
                if self._mode_for(problem) == "ai_suggest_confirm":
                    record = store.get_by_problem(self.account_id, self.lessonid, problemid)
                    if record and store.transition(self.account_id, record['id'], ('NEEDS_MANUAL',), 'SUGGESTING'):
                        threading.Thread(target=self._suggest_problem, args=(record['id'], dict(problem)), daemon=True).start()
        except Exception:
            logger.exception("Could not persist quiz; submission remains disabled")
            # Notifications remain available even if the local quiz database fails.
            if problemid not in self._skip_notified:
                self._skip_notified.add(problemid)
                self.on_event("problem_received", {"lesson": self.lessonname,
                    "lessonid": self.lessonid, "problemid": problemid})

    def _suggest_problem(self, quiz_id: str, problem: dict) -> None:
        from quiz import store
        from quiz.validation import validate_answer
        try:
            from ai_gateway import suggest
            result = suggest(self.account_id, problem)
            answer = validate_answer(problem, result['answer'])
            changed = store.transition(self.account_id, quiz_id, ('SUGGESTING',), 'AWAITING_CONFIRMATION', {
                'suggested_answer': answer, 'model_id': result.get('model_id'),
                'provider_id': result.get('provider_id'), 'latency_ms': result.get('latency_ms'),
                'error_code': None})
            if changed:
                self.on_event('quiz_suggestion_ready', {'lesson': self.lessonname, 'lessonid': self.lessonid, 'problemid': problem['problemId']})
        except Exception as exc:
            code = getattr(exc, 'code', 'AI_UNAVAILABLE')
            if not isinstance(code, str) or not code.replace('_', '').isalnum():
                code = 'AI_UNAVAILABLE'
            if store.transition(self.account_id, quiz_id, ('SUGGESTING',), 'NEEDS_MANUAL', {'error_code': code}):
                self._emit_ai_failed(problem['problemId'], problem['problemType'], code)

    def _reconcile_unlocked(self, data: dict) -> None:
        """Answer anything the server reports as unlocked that we haven't.

        Frames carry the full `unlockedproblem` list, the only way to recover a
        problem whose unlock frame we never saw — the WS drops every ~40-60s and
        anything opened in that gap would otherwise go unanswered.
        """
        for raw_id in data.get("unlockedproblem") or []:
            if self._failures.get(raw_id, 0) >= _MAX_SUBMIT_FAILS:
                continue
            problem = self._problems.get(raw_id)
            if problem is not None:
                with self._answered_lock:
                    if problem["problemId"] in self._answered:
                        continue
            # Unknown, or known-but-unanswered: re-read from the server so we
            # act on a fresh `result` and pick up problems we never fetched.
            self._load_presentations([(problem or {}).get("_pres") or data.get("presentation")])
            problem = self._problems.get(raw_id)
            if problem is None:
                self._failures[raw_id] = self._failures.get(raw_id, 0) + 1
            self._answer_if_needed(
                problem,
                self._remaining_limit(problem) if problem else 0,
                context=f"unlocked {raw_id}",
            )

    # ------------------------------------------------------------------
    # Answer building
    # ------------------------------------------------------------------

    def _submit_answer(self, problemid: Any, problemtype: int, real_answer: str | list[str], source: str) -> bool:
        # This legacy entry point is permanently disabled. Confirmed submission
        # must only be introduced through a separately verified workflow.
        logger.warning("Answer submission blocked: confirmation workflow is unavailable")
        return False

    def _emit_ai_failed(self, problemid: Any, problemtype: int, message: str) -> None:
        self.on_event("problem", {
            "lesson": self.lessonname, "lessonid": self.lessonid,
            "problemid": problemid, "problemtype": problemtype,
            "status": "ai_failed", "message": message,
        })

    # ------------------------------------------------------------------
    # Danmu
    # ------------------------------------------------------------------

    def _handle_danmu(self, content: str) -> None:
        if not self.course_config.get("auto_danmu", True):
            return

        key = content.lower().strip()
        now = time.time()
        self.danmu_dict[key] = [t for t in self.danmu_dict.get(key, []) if now - t <= 60]

        if now - self.sent_danmu_dict.get(key, 0) <= 60:
            return

        danmu_limit = max(1, self.course_config.get("danmu_threshold", 3))
        if len(self.danmu_dict[key]) + 1 >= danmu_limit:
            self.danmu_dict[key] = []
            self.sent_danmu_dict[key] = now
            threading.Thread(target=self.send_danmu, args=(content,), daemon=True).start()
        else:
            self.danmu_dict[key].append(now)

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_open(self, wsapp: websocket.WebSocketApp) -> None:
        self.ws_health.update(state="CONNECTED", last_connected_at=datetime.now(timezone.utc).isoformat())
        self.on_event("monitor_health_changed", {"classroom_health": {"lesson_id": self.lessonid, **self.ws_health}})
        wsapp.send(json.dumps({
            "op": "hello",
            "userid": self.user_uid,
            "role": "student",
            "auth": self.auth,
            "lessonid": self.lessonid,
        }))

    def _on_message(self, wsapp: websocket.WebSocketApp, message: str) -> None:
        self.ws_health.update(last_frame_at=datetime.now(timezone.utc).isoformat())
        self._frames += 1
        try:
            self._dispatch(json.loads(message))
        except Exception:
            # websocket-client swallows callback errors; log them here so a bad
            # frame never disappears silently.
            logger.exception(f"[{self.account_id}][WS {self.lessonname}] frame handling failed")

    def _dispatch(self, data: dict) -> None:
        op = data.get("op", "")
        logger.info(f"[{self.account_id}][WS {self.lessonname}] op={op}")

        # Frames carry server timestamps; keep the clock offset current.
        problem_ref = data.get("problem")
        dt = data.get("dt") or (problem_ref.get("dt") if isinstance(problem_ref, dict) else None)
        if isinstance(dt, (int, float)) and dt > 0:
            self._server_offset = max(self._server_offset, dt / 1000 - time.time())

        if op == "hello":
            # Re-read every presentation on each (re)connect: refreshes `result`
            # and repairs anything a failed fetch left missing.
            timeline = data.get("timeline", [])
            presentation_ids = {
                slide["pres"] for slide in timeline
                if slide.get("type") == "slide" and "pres" in slide
            }
            presentation_ids.add(data.get("presentation"))
            self._load_presentations(presentation_ids)

        elif op == "unlockproblem":
            # The frame names the problem by `prob` and its slide by `sid`;
            # either can be the key we hold it under.
            raw_id = problem_ref.get("prob") or problem_ref.get("sid")
            problem = self._problems.get(raw_id) or self._problems.get(problem_ref.get("sid"))
            if problem is None:
                # Its presentation is named right here — fetch on demand rather
                # than dropping the problem.
                self._load_presentations({problem_ref.get("pres"), data.get("presentation")})
                problem = self._problems.get(raw_id) or self._problems.get(problem_ref.get("sid"))
            self._answer_if_needed(problem, problem_ref.get("limit", -1) - 1, context=f"unlockproblem {raw_id}")

        elif op == "lessonfinished":
            self._stop(_Stop.FINISHED)
            if self.wsapp:
                self.wsapp.close()
            return

        elif op in ("presentationupdated", "presentationcreated", "showpresentation"):
            self._load_presentations([data.get("presentation")])

        elif op == "newdanmu":
            content = data.get("danmu", "")
            if content:
                self._handle_danmu(content)

        elif op == "gainbonus":
            logger.info(f"[{self.account_id}][WS {self.lessonname}] gainbonus raw: {data}")
            redpacket = data.get("redpacket", data)
            red_envelope_id = redpacket.get("redEnvelopeId")
            if red_envelope_id and self.course_config.get("auto_redpacket", True):
                threading.Thread(
                    target=self._grab_red_packet,
                    args=(red_envelope_id,),
                    daemon=True,
                ).start()

        elif op == "callpaused":
            if data.get("name") == self.user_uname:
                self.on_event("call", {"lesson": self.lessonname, "lessonid": self.lessonid})

        # Any frame may carry the full unlocked list — the safety net that
        # catches problems whose unlock frame we missed.
        self._reconcile_unlocked(data)
