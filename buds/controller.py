"""Serialised access to one pair of earbuds.

The transport is a single request/response serial link with a shared receive
buffer, so two overlapping reads will steal each other's replies: the first
caller gets an answer and the rest come back empty.  Every operation therefore
goes through one worker thread, strictly in order, and results are handed back
to the caller's thread through a queue.

A UI drives this by calling :meth:`submit` to queue work and :meth:`poll` from
its event loop to deliver whatever has finished.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .device import Buds, NotConnected, Port, find_ports

Callback = Callable[["Result"], None]


@dataclass
class Result:
    """Outcome of one queued operation."""

    task: str
    value: Any = None
    error: Exception | None = None
    disconnected: bool = False
    callback: Callback | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def message(self) -> str:
        return "" if self.ok else str(self.error)


@dataclass
class _Job:
    task: str
    fn: Callable[[Buds | None], Any]
    callback: Callback | None = None
    # Connecting is the one job that must run while nothing is connected yet.
    needs_connection: bool = True
    # Set for blocking callers: the result goes into ``slot`` and ``done`` is
    # signalled, instead of being queued for a poll() that may never happen.
    done: threading.Event | None = None
    slot: dict | None = None


_STOP = _Job("stop", lambda _b: None)


class Controller:
    """Owns the connection and the single worker thread behind it."""

    def __init__(self) -> None:
        self._buds: Buds | None = None
        self._jobs: queue.Queue[_Job] = queue.Queue()
        self._results: queue.Queue[Result] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._pending = 0
        self.port: Port | None = None

    # -------------------------------------------------------------- state --
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def busy(self) -> bool:
        """True until every queued job has finished *and* been delivered.

        Counting only the job queue would report idle the moment the last job
        was picked up, while it was still running and its result had yet to
        reach the caller.
        """
        with self._lock:
            return self._pending > 0

    def _track(self, delta: int) -> None:
        with self._lock:
            self._pending = max(0, self._pending + delta)

    # ---------------------------------------------------------- lifecycle --
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._loop, daemon=True,
                                        name="buds-worker")
        self._worker.start()

    def stop(self) -> None:
        if self._worker and self._worker.is_alive():
            self._jobs.put(_STOP)
            self._worker.join(timeout=3)
        self._drop()
        with self._lock:
            self._pending = 0

    def _drop(self) -> None:
        with self._lock:
            if self._buds:
                self._buds.close()
            self._buds = None
        self._connected.clear()

    # ------------------------------------------------------------- worker --
    def _deliver(self, job: _Job, result: Result) -> None:
        if job.done is not None:
            # Blocking caller: hand the result straight back and release it.
            if job.slot is not None:
                job.slot["result"] = result
            self._track(-1)
            job.done.set()
        else:
            self._results.put(result)

    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                if job is _STOP:
                    return
                buds = self._buds
                if job.needs_connection and (buds is None or not buds.connected):
                    raise NotConnected("earbuds are not connected")
                value = job.fn(buds)
                self._deliver(job, Result(job.task, value, callback=job.callback))
            except NotConnected as exc:
                # The link is genuinely gone: drop it and say so.
                self._drop()
                self._deliver(job, Result(job.task, error=exc, disconnected=True,
                                          callback=job.callback))
            except Exception as exc:
                # One operation failed but the connection may be fine, so keep
                # it and report only this failure.
                self._deliver(job, Result(job.task, error=exc,
                                          callback=job.callback))
            finally:
                self._jobs.task_done()

    # ---------------------------------------------------------------- api --
    def submit(self, task: str, fn: Callable[[Buds], Any],
               callback: Callback | None = None) -> None:
        """Queue an operation. ``fn`` receives the live :class:`Buds`."""
        self.start()
        self._track(+1)
        self._jobs.put(_Job(task, fn, callback))

    def _connect_fn(self, port: Port) -> Callable[[Buds | None], str]:
        """Build the connect operation, shared by the queued and blocking paths."""

        def do_connect(_unused: Buds | None) -> str:
            self._drop()
            buds = Buds(port.device)
            buds.connect()
            # Prove the link answers before calling it connected: the port can
            # open while nothing is listening on the other end.
            firmware = buds.firmware()
            if not firmware:
                buds.close()
                raise NotConnected(
                    f"{port.device} opened but the earbuds did not reply. "
                    "They are probably connected to another device right now."
                )
            with self._lock:
                self._buds = buds
            self.port = port
            self._connected.set()
            return firmware

        return do_connect

    def connect(self, port: Port, callback: Callback | None = None) -> None:
        """Open a connection, replacing any existing one."""
        self.start()
        self._track(+1)
        self._jobs.put(_Job("connect", self._connect_fn(port), callback,
                            needs_connection=False))

    def disconnect(self, callback: Callback | None = None) -> None:
        def do_disconnect(_unused: Buds | None) -> None:
            self._drop()

        self.start()
        self._track(+1)
        self._jobs.put(_Job("disconnect", do_disconnect, callback,
                            needs_connection=False))

    def execute(self, task: str, fn: Callable[[Buds], Any],
                timeout: float = 25.0, needs_connection: bool = True) -> Result:
        """Queue an operation and wait for it.

        For callers without an event loop to drive :meth:`poll`, such as an
        HTTP handler.  The work still runs on the one worker thread, so it
        stays ordered against everything else.
        """
        self.start()
        done = threading.Event()
        slot: dict = {}
        self._track(+1)
        self._jobs.put(_Job(task, fn, needs_connection=needs_connection,
                            done=done, slot=slot))
        if not done.wait(timeout):
            return Result(task, error=TimeoutError(
                f"{task} did not finish within {timeout:g}s"))
        return slot.get("result", Result(task, error=RuntimeError("no result")))

    def connect_sync(self, port: Port, timeout: float = 25.0) -> Result:
        """Blocking connect, for the same callers as :meth:`execute`."""
        return self.execute("connect", self._connect_fn(port), timeout,
                            needs_connection=False)

    def disconnect_sync(self, timeout: float = 10.0) -> Result:
        return self.execute("disconnect", lambda _b: self._drop(), timeout,
                            needs_connection=False)

    def poll(self) -> list[Result]:
        """Deliver finished results. Call this from the UI thread.

        Each result's callback runs here, on the caller's thread, so handlers
        can touch widgets safely.
        """
        done: list[Result] = []
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                break
            done.append(result)
            try:
                if result.callback:
                    result.callback(result)
            finally:
                # Counted down only once the handler has had the result, so
                # busy stays true for the whole round trip.
                self._track(-1)
        return done

    @staticmethod
    def ports() -> list[Port]:
        return find_ports()
