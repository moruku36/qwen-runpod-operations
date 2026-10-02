"""Cooperative test cancellation and a Linux main-thread deadline for diagnostic export."""
from __future__ import annotations
import contextlib
import os
import signal
import time
from pathlib import Path


class WorkInterrupted(BaseException):
    """Bypass ordinary backend Exception catches, then finalize a diagnostic report."""


def check_work(deadline: float | None = None, cancel: Path | None = None) -> None:
    if (deadline is not None and time.time() >= deadline) or (cancel is not None and cancel.exists()):
        raise WorkInterrupted("test deadline or cancellation reached")


@contextlib.contextmanager
def work_guard(deadline: float | None = None, cancel: Path | None = None):
    """Stop work at the inner deadline; outer bootstrap gives a separate export reserve.

    Linux signals interrupt blocked main-thread calls too. Windows only has cooperative
    checks; the paid bootstrap targets Linux. No guarantee against uninterruptible I/O.
    """
    check_work(deadline, cancel)
    if os.name != "posix":
        yield
        return
    def stop(_signum, _frame):
        raise WorkInterrupted("test deadline or termination signal")
    old_term = signal.signal(signal.SIGTERM, stop)
    old_alarm = signal.signal(signal.SIGALRM, stop)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    if deadline is not None:
        signal.setitimer(signal.ITIMER_REAL, max(.001, deadline-time.time()))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGALRM, old_alarm)
        if old_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(.001, old_timer[0]-(time.monotonic()-started)), old_timer[1])
