"""Start the local console, photo-agent watcher, and Spectrum iMessage bridge."""
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    bridge = ROOT / "spectrum"
    required = [(ROOT / "app/data/catalog.json", "run: .venv/bin/python -m live.setup"),
                (bridge / ".env", "configure spectrum/.env from spectrum/.env.example"),
                (bridge / "node_modules", "run: cd spectrum && npm install")]
    for path, instruction in required:
        if not path.exists():
            sys.exit(f"Setup needed: {instruction}")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 4175)) == 0:
            sys.exit("Port 4175 is already in use. Stop the existing console and watcher before starting the combined demo.")
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "live.serve"], cwd=ROOT, start_new_session=True))
        for _ in range(100):
            if children[0].poll() is not None:
                raise RuntimeError("Console exited during startup.")
            try:
                with urllib.request.urlopen("http://localhost:4175/api/health", timeout=1) as res:
                    if res.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        else:
            raise RuntimeError("Console did not start on port 4175.")
        children.append(subprocess.Popen([sys.executable, "-m", "live.run", "--watch"], cwd=ROOT, start_new_session=True))
        children.append(subprocess.Popen(["npm", "run", "start"], cwd=bridge, env=os.environ.copy(), start_new_session=True))
        print("Demo running: http://localhost:4175/live.html (Ctrl+C stops all three processes)", flush=True)
        while True:
            for child in children:
                if child.poll() is not None:
                    raise RuntimeError(f"A demo component exited (status {child.returncode}).")
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        for child in reversed(children):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
