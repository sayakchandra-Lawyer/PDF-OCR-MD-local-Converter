"""Background watcher: converts PDFs as soon as they land in a project folder."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

from pdf2md.config import WATCHER_STATE, Config
from pdf2md.core.fsutil import atomic_write
from pdf2md.core.layout import find_project, iter_projects
from pdf2md.service import Service

log = logging.getLogger("pdf2md")


def _is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


class Watcher:
    def __init__(self, cfg: Config, service: Service | None = None):
        self.cfg = cfg
        self.service = service or Service(cfg)
        self._pending: dict[Path, float] = {}
        self._pending_lock = threading.Lock()
        self._jobs: "queue.Queue[Path]" = queue.Queue()
        self._queued: set[Path] = set()
        self._stop = threading.Event()
        self._current: str | None = None
        self._started = time.time()
        self._done = 0
        self._failed = 0

    # ------------------------------------------------------------ events
    def notice(self, path: str | Path) -> None:
        if not _is_pdf(str(path)):
            return
        p = Path(path)
        with self._pending_lock:
            self._pending[p] = time.monotonic()

    def _settled(self) -> list[Path]:
        now = time.monotonic()
        ready = []
        with self._pending_lock:
            for p, t in list(self._pending.items()):
                if now - t >= self.cfg.watch.settle_seconds:
                    del self._pending[p]
                    ready.append(p)
        out = []
        for p in ready:
            try:
                s1 = p.stat().st_size
                time.sleep(0.5)
                s2 = p.stat().st_size
            except FileNotFoundError:
                continue
            if s1 != s2 or s1 == 0:
                self.notice(p)  # still being written / synced
                continue
            out.append(p)
        return out

    def _enqueue(self, p: Path) -> None:
        p = p.resolve()
        if p not in self._queued:
            self._queued.add(p)
            self._jobs.put(p)

    # ---------------------------------------------------------- workers
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                p = self._jobs.get(timeout=1)
            except queue.Empty:
                continue
            self._queued.discard(p)
            if not p.exists():
                continue
            layout = find_project(p, self.cfg)
            if layout is None:
                log.info("ignoring %s (not inside a project folder)", p)
                continue
            self._current = str(p)
            self._write_state()
            try:
                res = self.service.process(p, layout.root, progress=lambda m: self._progress(p, m))
                log.info(res.summary())
                if res.status == "failed":
                    self._failed += 1
                elif res.status == "converted":
                    self._done += 1
            except Exception:  # noqa: BLE001
                log.exception("error processing %s", p)
                self._failed += 1
            finally:
                self._current = None
                self._write_state()

    def _progress(self, p: Path, msg: str) -> None:
        self._current = f"{p} :: {msg}"
        log.info("%s: %s", p.name, msg)
        self._write_state()

    def _write_state(self) -> None:
        state = {
            "pid": os.getpid(),
            "started": self._started,
            "heartbeat": time.time(),
            "current": self._current,
            "queue": self._jobs.qsize(),
            "converted": self._done,
            "failed": self._failed,
        }
        try:
            atomic_write(WATCHER_STATE, json.dumps(state, indent=2))
        except OSError:
            pass

    def rescan(self) -> None:
        for layout in iter_projects(self.cfg):
            try:
                pdfs = list(layout.iter_pdfs())
                if not pdfs and not layout.manifest_path.exists():
                    continue  # leave projects without PDFs untouched
                for pdf in pdfs:
                    self._enqueue(pdf)
                self.service.mark_missing(layout)
            except OSError as e:
                log.warning("cannot scan %s: %s", layout.root, e)

    # --------------------------------------------------------------- run
    def run(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserver

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    watcher.notice(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    watcher.notice(event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    watcher.notice(event.dest_path)

        parents, projects = self.cfg.watch_roots()
        roots = [r for r in [*parents, *projects] if r.is_dir()]
        missing = [r for r in [*parents, *projects] if not r.is_dir()]
        for r in missing:
            log.warning("watch folder does not exist: %s", r)
        if not roots:
            log.error("Nothing to watch. Add folders with: pdf2md add-folder <path>")
        observer = PollingObserver(timeout=5) if self.cfg.watch.use_polling else Observer()
        handler = Handler()
        for r in roots:
            observer.schedule(handler, str(r), recursive=True)
            log.info("watching %s", r)
        observer.start()
        threading.Thread(target=self._worker, name="pdf2md-worker", daemon=True).start()

        self.rescan()
        last_scan = time.monotonic()
        try:
            while not self._stop.is_set():
                for p in self._settled():
                    self._enqueue(p)
                if time.monotonic() - last_scan > self.cfg.watch.rescan_minutes * 60:
                    self.rescan()
                    last_scan = time.monotonic()
                self._write_state()
                self._stop.wait(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            observer.stop()
            observer.join(timeout=5)

    def stop(self) -> None:
        self._stop.set()


def watcher_state() -> dict | None:
    try:
        state = json.loads(WATCHER_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    state["alive"] = time.time() - state.get("heartbeat", 0) < 30
    return state
