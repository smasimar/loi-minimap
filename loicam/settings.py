"""User-tunable camera settings, persisted to loi_cam.json next to loi_cam.py."""
import json
import os
from dataclasses import asdict, dataclass

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "loi_cam.json")


@dataclass
class Settings:
    sens: float = 1.0        # multiplier on orbit turn speed
    distance: float = 1500.0
    lift: float = 450.0      # extra eye height; sets how steeply the camera looks down
    focal: int = 535         # focal length in px; lower = wider FoV (game default ~835)

    LIMITS = {
        "sens": (0.2, 4.0),
        "distance": (300.0, 4000.0),
        "lift": (-400.0, 1500.0),
        "focal": (350, 1400),
    }

    def adjust(self, name, step):
        if name == "reset":
            fresh = Settings()
            for k in asdict(fresh):
                setattr(self, k, getattr(fresh, k))
            return
        lo, hi = self.LIMITS[name]
        value = min(hi, max(lo, getattr(self, name) + step))
        setattr(self, name, round(value, 2) if isinstance(value, float) else int(value))

    def describe(self):
        import math
        hfov = math.degrees(2 * math.atan(320 / self.focal))
        return (f"sens {self.sens:.1f}x | distance {self.distance:.0f} | angle/height {self.lift:.0f} "
                f"| FoV {hfov:.0f} deg (focal {self.focal})")

    def save(self):
        try:
            with open(PATH, "w") as f:
                json.dump(asdict(self), f, indent=2)
        except OSError as e:
            print(f"could not save settings: {e}")

    @classmethod
    def load(cls):
        s = cls()
        try:
            with open(PATH) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return s
        for k, v in data.items():
            if k in asdict(s):
                setattr(s, k, type(getattr(s, k))(v))
        return s
