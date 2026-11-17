"""The stages of a running answer, for the page that waits for it.

The page sends a ``progress_id`` with a question and reads the stages while the answer runs.
The harness reports its internal steps (``read``, ``route``, ``write``, ``check``, ...). The
reader sees only the plain stage of each step, and a stage never goes back: a second draft or
a second try with a stronger tier stays in the stage that runs. The reader does not need to
know which component does the work.
A turn with no ``progress_id`` records nothing. The record is in memory only: it is for
display, not for audit (``llm_usage`` keeps the cost of each turn).

The same module stops a duplicate: a question that is still running for a user cannot
start again until it ends.
"""
import threading
import time
from dataclasses import dataclass, field

from pydantic import BaseModel

KEEP_SECONDS = 15 * 60
MAX_RUNS = 1000

# The stages that the reader sees, in their order.
STAGES: list[tuple[str, str]] = [
    ("understand", "Understand your question"),
    ("numbers", "Get the numbers"),
    ("write", "Write the report and check each statement"),
    ("save", "Save the report"),
]
# Each internal step and its stage.
_STAGE_OF = {
    "read": "understand", "decide": "understand", "route": "understand",
    "cache": "numbers", "query": "numbers", "brief": "numbers", "sql": "numbers", "code": "numbers",
    "write": "write", "check": "write", "escalate": "write",
    "save": "save",
}
_ORDER = {key: n for n, (key, _) in enumerate(STAGES)}
_LABEL = dict(STAGES)


class StepView(BaseModel):
    key: str
    label: str
    # None while the step runs.
    seconds: float | None = None


class ProgressView(BaseModel):
    steps: list[StepView]
    seconds: float
    done: bool


@dataclass
class _Step:
    key: str
    label: str
    started: float
    ended: float | None = None


@dataclass
class _Run:
    started: float
    steps: list[_Step] = field(default_factory=list)
    ended: float | None = None


_lock = threading.Lock()
_runs: dict[str, _Run] = {}  # by turn id
_ids: dict[str, str] = {}  # progress id -> turn id
_running: dict[str, str] = {}  # duplicate key -> turn id
_keys: dict[str, str] = {}  # turn id -> duplicate key


def _now() -> float:
    return time.monotonic()


def _prune(now: float) -> None:
    for turn_id in [t for t, r in _runs.items() if now - r.started > KEEP_SECONDS]:
        del _runs[turn_id]
    while len(_runs) >= MAX_RUNS:  # the oldest first: a dict keeps the insertion order
        del _runs[next(iter(_runs))]
    for pid in [p for p, t in _ids.items() if t not in _runs]:
        del _ids[pid]


def _release(turn_id: str) -> None:
    key = _keys.pop(turn_id, None)
    if key is not None and _running.get(key) == turn_id:
        del _running[key]


def duplicate_key(user: str, source_id: str, report_id: str | None, question: str, extra: str = "") -> str:
    """The same user, source, report, and question (case and spaces do not count)."""
    words = " ".join(question.lower().split())
    return "|".join([user, source_id, report_id or "", words, extra])


def start(turn_id: str, duplicate_key: str | None = None) -> bool:
    """Mark a turn as running. False when ``duplicate_key`` is already running."""
    with _lock:
        if duplicate_key is not None:
            if duplicate_key in _running:
                return False
            _running[duplicate_key] = turn_id
            _keys[turn_id] = duplicate_key
        return True


def track(progress_id: str | None, turn_id: str) -> None:
    """Record the steps of ``turn_id`` under ``progress_id``. No id: record nothing."""
    if progress_id is None:
        return
    with _lock:
        now = _now()
        _prune(now)
        _runs[turn_id] = _Run(started=now)
        _ids[progress_id] = turn_id


def step(turn_id: str, key: str) -> None:
    """Record the internal step ``key``. A new stage closes the open stage and opens.

    A step of the open stage, or of an earlier stage, changes nothing."""
    stage = _STAGE_OF[key]
    with _lock:
        run = _runs.get(turn_id)
        if run is None:
            return
        if run.steps and _ORDER[stage] <= _ORDER[run.steps[-1].key]:
            return
        now = _now()
        if run.steps and run.steps[-1].ended is None:
            run.steps[-1].ended = now
        run.steps.append(_Step(stage, _LABEL[stage], now))


def finish(turn_id: str) -> None:
    with _lock:
        _release(turn_id)
        run = _runs.get(turn_id)
        if run is None:
            return
        now = _now()
        if run.steps and run.steps[-1].ended is None:
            run.steps[-1].ended = now
        run.ended = now


def snapshot(progress_id: str) -> ProgressView | None:
    with _lock:
        run = _runs.get(_ids.get(progress_id, ""))
        if run is None:
            return None
        now = _now()
        steps = [
            StepView(key=s.key, label=s.label,
                     seconds=None if s.ended is None else round(s.ended - s.started, 1))
            for s in run.steps
        ]
        end = run.ended if run.ended is not None else now
        return ProgressView(steps=steps, seconds=round(end - run.started, 1), done=run.ended is not None)


def clear() -> None:
    """For tests."""
    with _lock:
        _runs.clear()
        _ids.clear()
        _running.clear()
        _keys.clear()
