"""Keep the Qt harnesses away from the real settings of the machine.

The harnesses open real windows that save choices through `aerotools.config`. Run on a workstation
they must never touch its `%LOCALAPPDATA%\\HELIX\\AeroTools\\settings.json`: `Guard` sends LOCALAPPDATA to a throw-away folder before
AeroTools is imported, `pin(config)` makes `config.settings_dir` return that folder whatever happens to the environment later, and
`check()` compares the real settings file with its state at the start (content and modification time).
"""

import hashlib
import os
import tempfile


def _real_settings():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HELIX", "AeroTools", "settings.json")


def _snapshot(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest(), os.path.getmtime(path)
    except OSError:
        return None


class Guard(object):
    def __init__(self, prefix):
        self.real_path = _real_settings()
        self.before = _snapshot(self.real_path)
        self.temp = tempfile.mkdtemp(prefix=prefix)
        os.environ["LOCALAPPDATA"] = self.temp

    def pin(self, config):
        """Fix the settings folder of `config` inside the throw-away folder."""
        folder = os.path.join(self.temp, "HELIX", "AeroTools")
        config.settings_dir = lambda: folder

    def check(self):
        """None when the real settings file is as it was at the start, else a sentence saying what changed."""
        after = _snapshot(self.real_path)
        if after == self.before:
            return None
        if self.before is None or after is None:
            return "the real settings file {} appeared or disappeared during the run".format(self.real_path)
        return "the real settings file {} was modified during the run".format(self.real_path)
