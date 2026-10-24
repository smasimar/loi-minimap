"""User-tunable camera settings, kept in the "camera" section of config.json."""
from dataclasses import asdict, dataclass


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

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        s = cls()
        for k, v in (data or {}).items():
            if k in asdict(s):
                setattr(s, k, type(getattr(s, k))(v))
        return s
