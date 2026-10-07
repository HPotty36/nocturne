"""Queue item rules: what an upload waiting in queue/<name>/item.json looks like and who may take it.

Pure functions, no I/O. Time is always passed in. The sha-guarded write of the claim lives in the worker.
"""
from datetime import datetime, timezone

WAIT_SECONDS = 60    # GitHub waits this long after the upload so the PC helper gets the first chance
STALE_SECONDS = 600  # a claim older than this is taken to be dead and may be taken again

_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_WORKERS = ("pc", "github")


def iso(dt):
    """"YYYY-MM-DDTHH:MM:SSZ" in UTC."""
    return dt.astimezone(timezone.utc).strftime(_FORMAT)


def parse_iso(s):
    return datetime.strptime(s, _FORMAT).replace(tzinfo=timezone.utc)


def new_item(name, *, kind, grayscale, now, date=None, camera=None, file=None):
    """item.json of a fresh upload; kind is "new" or "redraft" (then `file` is the photo to rewrite)."""
    return {"name": name, "kind": kind, "file": file, "uploaded_at": iso(now), "date": date, "camera": camera,
            "grayscale": grayscale, "status": "waiting", "claimed_at": None, "by": None, "error": None}


def _age(stamp, now):
    return (now - parse_iso(stamp)).total_seconds()


def is_stale(item, now):
    """Claimed by pc or github more than STALE_SECONDS ago. A claim without a time counts as stale."""
    if item["status"] not in _WORKERS:
        return False
    return not item.get("claimed_at") or _age(item["claimed_at"], now) > STALE_SECONDS


def can_take(item, now, by):
    """May worker `by` ("pc" or "github") claim this item now? A failed item is never taken again by itself."""
    if by not in _WORKERS:
        raise ValueError(f"by는 pc 또는 github여야 해요: {by!r}")
    if is_stale(item, now):
        return True
    if item["status"] != "waiting":
        return False
    return by == "pc" or _age(item["uploaded_at"], now) >= WAIT_SECONDS


def claim(item, by, now):
    return {**item, "status": by, "by": by, "claimed_at": iso(now), "error": None}


def fail(item, error):
    return {**item, "status": "failed", "error": error}


def release(item, note):
    """The item handed back unclaimed, waiting for any worker again; `note` (why) is kept in `error`, uploaded_at stays."""
    return {**item, "status": "waiting", "claimed_at": None, "by": None, "error": note}
