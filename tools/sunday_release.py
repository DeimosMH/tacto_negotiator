"""Fetch the Sunday data release as soon as the arena publishes it (scheduled for 08:10, see
docs/FINAL_RUNBOOK.md). Downloads only `release-sunday` into data/release-sunday (the tracked package is
not touched), checks that DataPackage merges it, runs the test suite, and reports by desktop notification
and in logs/sunday-release.log.

    .venv/bin/python tools/sunday_release.py            # retry every 60 s until it is public (up to 10:30)
    .venv/bin/python tools/sunday_release.py --once     # one attempt
"""

import argparse
import io
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tacto_negotiator.cli import load_env  # noqa: E402
from tacto_negotiator.client import ArenaClient  # noqa: E402

NAME = "release-sunday"
LOG = ROOT / "logs" / "sunday-release.log"
log = logging.getLogger("sunday-release")


def notify(title, text, urgent=False):
    log.info("%s: %s", title, text)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-u", "critical" if urgent else "normal", "-a", "tacto", title, text],
                       check=False, timeout=10)


def fetch(client, target):
    """Download and unpack the release; True once data/release-sunday/facts.json is in place."""
    listing = client.downloads()
    info = listing.get(NAME) or {}
    if not info.get("url"):
        log.info("not public yet: %s", json.dumps(info))
        return False
    payload = client.download(NAME)
    with tempfile.TemporaryDirectory(dir=target) as temporary:
        zipfile.ZipFile(io.BytesIO(payload)).extractall(temporary)
        found = next(Path(temporary).rglob("facts.json"), None)
        if found is None:
            raise RuntimeError("the release zip has no facts.json")
        destination = target / NAME
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(found.parent, destination)
    log.info("downloaded %d bytes into %s", len(payload), destination)
    return True


def verify():
    from tacto_negotiator.data import DataPackage

    package = DataPackage(ROOT / "data")
    if package.release is None:
        raise RuntimeError("DataPackage does not see the release")
    facts = json.loads((package.release / "facts.json").read_text(encoding="utf-8"))
    files = sum(1 for p in package.release.rglob("*") if p.is_file())
    return f"{len(facts)} new citable facts, {files} files in {package.release.relative_to(ROOT)}"


def tests():
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "docs/test_docs.py", "tests"], cwd=ROOT,
                         capture_output=True, text=True, timeout=600)
    last = (run.stdout.strip().splitlines() or ["no output"])[-1]
    return run.returncode == 0, last


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="one attempt, no waiting")
    parser.add_argument("--until", default="10:30", help="stop retrying at this local time (HH:MM)")
    args = parser.parse_args()
    LOG.parent.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler()])
    load_env(ROOT / ".env")
    client = ArenaClient()
    stop = datetime.combine(datetime.now().date(), datetime.strptime(args.until, "%H:%M").time())
    while True:
        try:
            if fetch(client, ROOT / "data"):
                break
        except Exception as error:  # network or arena hiccup: try again
            log.warning("attempt failed: %s: %s", type(error).__name__, error)
        if args.once or datetime.now() >= stop:
            notify("Tacto: Sunday release NOT downloaded", f"Run: .venv/bin/tacto data --out data (see {LOG})",
                   urgent=True)
            return 1
        time.sleep(60)
    try:
        summary = verify()
    except Exception as error:
        notify("Tacto: Sunday release check failed", str(error), urgent=True)
        return 1
    passed, last = tests()
    notify("Tacto: Sunday release ready" if passed else "Tacto: release in place, TESTS FAILED",
           f"{summary}. Tests: {last}", urgent=not passed)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
