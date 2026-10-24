"""Settings for both tools, kept in one config.json next to loi_tools.py:

    {"pine_slot": 28011, "minimap": {...}, "camera": {...}}

Hotkey changes are saved immediately. Older separate files (the minimap's flat config.json and
the camera's loi_cam.json) are migrated on first load.
"""

import json
import os
import threading

from . import CONFIG_FILE, ROOT

LEGACY_CAMERA_FILE = os.path.join(ROOT, "loi_cam.json")


class Config:
    def __init__(self, data):
        self.data = data
        self._lock = threading.Lock()

    @property
    def pine_slot(self):
        return self.data.get("pine_slot", 28011)

    @property
    def minimap(self):
        """The minimap's settings dict (edited in place, then save())."""
        return self.data.setdefault("minimap", {})

    @property
    def camera(self):
        """The camera's settings dict (edited in place, then save())."""
        return self.data.setdefault("camera", {})

    def save(self):
        # Called from both tools' threads; cloud sync (e.g. Dropbox) may briefly lock the file.
        with self._lock:
            tmp = CONFIG_FILE + ".tmp"
            try:
                with open(tmp, "w") as f:
                    json.dump(self.data, f, indent=1)
                os.replace(tmp, CONFIG_FILE)
            except OSError as e:
                print(f"could not save settings: {e}")

    @classmethod
    def load(cls):
        data = _read(CONFIG_FILE) or {}
        migrated = False
        if data and "minimap" not in data and "camera" not in data:  # old flat minimap config
            slot = data.pop("pine_slot", None)
            for research_only in ("record", "trace"):
                data.pop(research_only, None)
            data = {"minimap": data}
            if slot:
                data["pine_slot"] = slot
            migrated = True
        if "camera" not in data:
            legacy = _read(LEGACY_CAMERA_FILE)
            if legacy:
                data["camera"] = legacy
                migrated = True
        cfg = cls(data)
        if migrated:
            cfg.save()
        return cfg


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
