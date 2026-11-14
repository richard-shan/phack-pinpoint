"""Start the dispatcher console with the team's keys.

    python3 -m live.serve            # http://localhost:4175  (use localhost, not 127.0.0.1)
    python3 -m live.serve --port 4180

The console reads its Matterport viewer key and Vexcel key from the environment. This
takes them from `api.txt` for the console process only; nothing is written to disk.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import keys

APP = Path(__file__).resolve().parent.parent / "app"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=4175)
    a = ap.parse_args()
    if not (APP / "data" / "catalog.json").exists():
        sys.exit("the console has no catalog yet: run `python3 -m live.setup` first")
    import socket
    with socket.socket() as probe:                       # say so plainly if something already has the port
        if probe.connect_ex(("127.0.0.1", a.port)) == 0:
            sys.exit(f"port {a.port} is already in use (another console?). Pick another: "
                     f"python3 -m live.serve --port {a.port + 5}   and then   --console http://localhost:{a.port + 5}")
    have = keys.export_env()
    print(f"keys available to the console: {', '.join(have) or 'none'}")
    print(f"console: http://localhost:{a.port}/live.html   (Ctrl+C to stop)", flush=True)
    env = dict(os.environ, PORT=str(a.port))
    try:
        sys.exit(subprocess.run(["node", "local-server.mjs"], cwd=APP, env=env, check=False).returncode)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
