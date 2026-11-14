"""Hand the team's API keys to the lanes that expect them under other names.

Lane A reads `api.txt` itself. Perception and the console read environment variables
(or their own `.env` files). This sets those variables for the running process, and for
processes it starts, from `api.txt`. Nothing is written to disk and nothing is printed.
A variable that is already set, for example by a teammate's own `.env`, is left alone.
"""
import os

NAMES = {                      # variable the other lanes read -> name in env/config.py
    "MATTERPORT_API_KEY": "MATTERPORT_KEY",
    "MATTERPORT_API_TOKEN": "MATTERPORT_TOKEN",
    "MATTERPORT_SDK_KEY": "MATTERPORT_SDK_KEY",
    "VEXCEL_API_KEY": "VEXCEL_API_KEY",
}


def export_env() -> list[str]:
    """Set the missing variables. -> the names that are now available."""
    from env import config
    have = []
    for theirs, ours in NAMES.items():
        if not os.environ.get(theirs):
            try:
                os.environ[theirs] = config.key(ours)
            except RuntimeError:
                continue
        have.append(theirs)
    return have
