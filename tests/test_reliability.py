"""Regression tests for the paths that must never drop a check-in or an answer.

Runs against a fake Yuketang — no network, no config, no pytest:

    python tests/test_reliability.py
"""

import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
_store = tempfile.TemporaryDirectory(prefix="yuketang-test-")  # removed at exit
os.environ.setdefault("YUKETANG_STORE_DIR", _store.name)

import config          # noqa: E402
import lesson as lesson_mod  # noqa: E402
import monitor as monitor_mod  # noqa: E402
import pushdeer  # noqa: E402
from ai_provider import QwenProvider  # noqa: E402
from lesson import Lesson     # noqa: E402

PROB, SLIDE, PRES = "prob-1", "slide-1", "pres-1"

HELLO = {"op": "hello", "timeline": [{"type": "slide", "pres": PRES}], "presentation": PRES}
UNLOCK = {"op": "unlockproblem", "unlockedproblem": [PROB],
          "problem": {"prob": PROB, "sid": SLIDE, "pres": PRES, "limit": 61}}

failures = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   <-- {extra}"))
    if not cond:
        failures.append(name)


class Fake:
    """Fake Yuketang. The flags steer one failure mode each."""

    def __init__(self, result=None, fail_fetch=0, answer_code=0, checkin_ok=True,
                 limit=0, send_ms=0, always_reject=False, cover="http://cover", ptype=1):
        self.result, self.fail_fetch, self.answer_code = result, fail_fetch, answer_code
        self.checkin_ok, self.limit, self.send_ms = checkin_ok, limit, send_ms
        self.always_reject, self.cover, self.ptype = always_reject, cover, ptype
        self.submissions, self.fetches = [], 0

    def __call__(self, method, url, **kw):
        if "checkin" in url:
            if not self.checkin_ok:
                raise RuntimeError("network down")
            return SimpleNamespace(headers={}, json=lambda: {"code": 0, "data": {"lessonToken": "tok"}})
        if "presentation/fetch" in url:
            self.fetches += 1
            if self.fetches <= self.fail_fetch:
                raise RuntimeError("fetch failed")
            return SimpleNamespace(headers={}, json=lambda: {"code": 0, "data": {"slides": [
                {"id": SLIDE, "cover": self.cover, "problem": {
                    "problemId": PROB, "problemType": self.ptype, "limit": self.limit,
                    "sendTime": self.send_ms, "result": self.result,
                    "options": [{"key": k, "value": k} for k in "ABCD"]}}]}})
        if "problem/answer" in url:
            self.submissions.append(kw["json"])
            code = 1 if self.always_reject else self.answer_code
            self.answer_code = 0  # only the first submit fails
            return SimpleNamespace(headers={}, json=lambda: {"code": code, "msg": "busy" if code else "ok"})
        raise AssertionError("unexpected url " + url)


def make_lesson(fake, **course_config):
    config.http_request = fake
    lesson_mod.http_request = fake
    lesson_mod.get_account = lambda aid: {"user": {"id": 1, "name": "u"}}
    events = []
    cfg = {"type1": "random", "answer_near_deadline": False, **course_config}
    les = Lesson("acct", {"lessonid": 9, "lessonname": "L", "classroomid": 3},
                 "sid", "pro.yuketang.cn", cfg, lambda t, d: events.append((t, d)))
    les.auth = "tok"
    return les, events


def settle():
    for t in threading.enumerate():
        if t.name.startswith("answer-"):
            t.join(timeout=10)
    time.sleep(0.05)


def stub_ai(behaviour):
    class Provider:
        def answer_options(self, *a, **k): return behaviour()
        def answer_short(self, *a, **k): return behaviour()
    lesson_mod.create_provider = lambda *a, **k: Provider()
    lesson_mod.get_ai_config = lambda aid: {"keys": [{"name": "k", "provider": "google", "key": "x"}],
                                            "active_key": 0, "fallback_keys": True}


# ---------------------------------------------------------------- answering

def test_answering():
    # Legacy automated submissions are no longer allowed. The same live and
    # recovery events must create one review task without posting an answer.
    from quiz import store
    dbdir = tempfile.TemporaryDirectory(prefix="review-regression-")
    old_db = store.DB
    store.DB = os.path.join(dbdir.name, "quizzes.sqlite3")
    try:
        f = Fake(); l, ev = make_lesson(f, type1="notify_only")
        l._dispatch(HELLO); l._dispatch(UNLOCK); l._dispatch(UNLOCK); settle()
        check("live repeated events never submit without confirmation", f.submissions == [], f.submissions)
        check("live repeated events create one review task", len(store.list_pending("acct")) == 1)
        check("initial problem notification is emitted once", sum(t == "problem_received" for t, _ in ev) == 1, ev)
        f = Fake(result=["A"]); l, ev = make_lesson(f, type1="notify_only")
        l._dispatch(HELLO); l._dispatch(UNLOCK); settle()
        check("answer completed elsewhere is never overwritten", not f.submissions)
        f = Fake(limit=60, send_ms=int((time.time() - 30) * 1000)); l, ev = make_lesson(f)
        l._dispatch(HELLO)
        check("deadline recovered from problem data", 28 <= l._remaining_limit(l._problems[PROB]) <= 30)
    finally:
        store.DB = old_db
        dbdir.cleanup()


def test_ai():
    # Verify parsing failure in the legacy provider without making a network call.
    qwen = QwenProvider.__new__(QwenProvider)
    qwen.model = "m"
    null_reply = SimpleNamespace(choices=None)
    qwen.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: null_reply)))
    try:
        qwen.answer_options("http://cover", ["A", "B"], 1, 1)
        reason = ""
    except RuntimeError as e:
        reason = str(e)
    check("a ModelScope reply without choices raises a readable error", "choices is null" in reason, reason)


# ----------------------------------------------------------------- check-in

def test_checkin():
    f = Fake(checkin_ok=False); l, ev = make_lesson(f)
    try:
        l._checkin()
        raised = False
    except Exception:
        raised = True
    check("a failed check-in raises so the next poll retries", raised)
    check("a failed check-in still notifies",
          ev and ev[0][0] == "signin" and ev[0][1]["status"] == "error", ev)

    m = monitor_mod.Monitor("acct", None)

    class DeadLesson:
        lessonid, lessonname, classroomid = 9, "L", 3
        def start_lesson(self): raise RuntimeError("check-in failed")

    m._active_lessons[9] = DeadLesson()
    m._lesson_thread(m._active_lessons[9])
    check("a dead lesson frees its slot for the next poll", m._active_lessons == {})


# -------------------------------------------------------------- poll loop

def test_poll_loop():
    def run_monitor(codes):
        seq, events, polls = iter(codes), [], []

        def fake(method, url, **kw):
            code = next(seq, 0)
            polls.append(code)
            return SimpleNamespace(status_code=401 if code == 401 else 200, headers={}, json=lambda: {
                "code": code, "msg": "server busy", "data": {"onLessonClassrooms": []}})

        monitor_mod.http_request = fake
        monitor_mod.get_domain = lambda aid: "pro.yuketang.cn"
        monitor_mod.get_sessionid = lambda aid: "sid"
        monitor_mod.get_poll_interval = lambda aid: 1
        m = monitor_mod.Monitor("acct", None)
        m._emit = lambda t, d: events.append(t)
        m._running = True
        threading.Thread(target=m._run, daemon=True).start()
        return m, events, polls

    m, events, polls = run_monitor([1, 1, 0, 0])
    time.sleep(3.4)
    check("a transient rejection doesn't stop monitoring",
          m._running and "session_expired" not in events, (events, polls))
    check("polling continues after a rejection", len(polls) >= 3, polls)
    m._running = False
    m._wake_event.set()

    pushed = []
    monitor_mod.pushdeer.send_session_expired = pushed.append
    m, events, polls = run_monitor([401, 401, 401, 401])
    time.sleep(3.4)
    check("a genuinely dead session expires after the cap",
          not m._running and events.count("session_expired") == 1, (events, polls))
    check("an expired session is pushed to the phone", pushed == ["acct"], pushed)


# ----------------------------------------------------------- course toggle

def test_course_toggle():
    enabled = {"on": True}
    monitor_mod.get_course_config = lambda aid, cid: {"course_enabled": enabled["on"]}

    class StubLesson:
        def __init__(self, lesson_data, **kw):
            self.lessonid, self.lessonname, self.classroomid = lesson_data["lessonid"], "L", 3
            self._stopped = threading.Event()
        def start_lesson(self): self._stopped.wait(5)
        def stop_lesson(self): self._stopped.set()

    monitor_mod.Lesson = StubLesson
    m, events = monitor_mod.Monitor("acct", None), []
    m._emit = lambda t, d: events.append(t)
    m._running = True
    poll = [{"lessonId": 9, "classroomId": 3, "courseName": "L"}]

    m._sync_lessons(poll, "pro.yuketang.cn", "sid")
    check("a live class of an enabled course is joined", list(m._active_lessons) == [9], m._active_lessons)
    enabled["on"] = False
    m._sync_lessons(poll, "pro.yuketang.cn", "sid")
    check("turning a course off mid-class leaves the class",
          m._active_lessons == {} and events == ["lesson_start", "lesson_end"], events)
    m._sync_lessons(poll, "pro.yuketang.cn", "sid")
    check("a live class of a disabled course is not joined", m._active_lessons == {} and len(events) == 2, events)

    enabled["on"] = True
    m._running = False  # stop() ran while the poll was in flight
    m._sync_lessons(poll, "pro.yuketang.cn", "sid")
    check("a stopped monitor registers no new lesson", m._active_lessons == {} and len(events) == 2, events)


# ---------------------------------------------------------------- push text

def test_push_text():
    title, body = pushdeer.format_event("problem", {"lesson": "L", "problemtype": 2, "status": "success",
                                                    "answers": ["A", "C"], "source": "ai"}, "en")
    check("push names the type once, in the title", title == "Multiple Choice" and body == "L: success, answer(s): A, C [AI]",
          (title, body))
    _, body = pushdeer.format_event("problem", {"lesson": "L", "problemtype": 2, "status": "ai_failed",
                                                "message": "slide has no cover image"}, "en")
    check("push carries the failure reason", body.endswith("\nReason: slide has no cover image"), body)


if __name__ == "__main__":
    test_answering()
    test_ai()
    test_checkin()
    test_poll_loop()
    test_course_toggle()
    test_push_text()
    print("\nALL PASS" if not failures else f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1 if failures else 0)
