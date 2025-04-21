from pathlib import Path
import time, typer
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from ca_core import ensure_dir
from issue_cert import issue_cert


class _Handler(FileSystemEventHandler):
    def __init__(self, file: Path, ca, certs):
        self.file, self.ca, self.certs = file, ca, certs
        self._known = set()

    def on_modified(self, event):
        self._process()

    def _process(self):
        ensure_dir(self.file.parent)
        domains = {d.strip() for d in self.file.read_text().splitlines() if d}
        new = domains - self._known
        for d in sorted(new):
            issue_cert(d, [], self.ca, self.certs)
        self._known |= new


def watch_file(file: Path, ca_dir: Path, certs_dir: Path):
    file = Path(file)
    ensure_dir(file.parent)
    file.touch(exist_ok=True)
    h = _Handler(file, ca_dir, certs_dir)
    h._process()

    obs = Observer()
    obs.schedule(h, str(file.parent), recursive=False)
    obs.start()
    typer.echo("[watch] Ctrl‑C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()
