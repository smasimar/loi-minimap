"""Orbit camera math. Game space is Y-down, so 'up' is -Y."""
import math
from dataclasses import dataclass


@dataclass
class OrbitConfig:
    distance: float = 1500.0
    lift: float = 450.0                   # extra eye height above the orbit; look-at stays on Leon
    min_pitch: float = math.radians(-20)   # below the follow point
    max_pitch: float = math.radians(70)
    yaw_speed: float = math.radians(225)   # rad/s at full deflection, before sens
    pitch_speed: float = math.radians(138)
    sens: float = 1.0
    deadzone: float = 0.18
    invert_x: bool = False
    invert_y: bool = False
    smoothing: float = 14.0                # 1/s; how fast the pivot catches up with Leon, 0 = locked


def _shape(v, dz):
    """Radial-ish deadzone with rescale and a mild curve for fine aim."""
    a = abs(v)
    if a < dz:
        return 0.0
    a = (a - dz) / (1.0 - dz)
    return math.copysign(a * a * 0.5 + a * 0.5, v)


class Orbit:
    def __init__(self, cfg: OrbitConfig):
        self.cfg = cfg
        self.yaw = 0.0
        self.pitch = math.radians(15)
        self.pivot = None

    def align_to(self, eye, target):
        """Start from wherever the game's camera is, so enabling doesn't snap."""
        dx, dy, dz = eye[0] - target[0], eye[1] - target[1], eye[2] - target[2]
        self.yaw = math.atan2(dx, dz)
        horiz = math.hypot(dx, dz)
        self.pitch = min(self.cfg.max_pitch, max(self.cfg.min_pitch, math.atan2(-dy, horiz)))
        self.pivot = None

    def place_behind(self, facing_yaw):
        """Swing round so the camera looks the way Leon faces; pitch is left as the player set it."""
        self.yaw = facing_yaw + math.pi
        self.pivot = None

    def stick_active(self, rx, ry):
        return abs(rx) >= self.cfg.deadzone or abs(ry) >= self.cfg.deadzone

    def update(self, dt, rx, ry, target):
        """Returns (eye, look_at)."""
        c = self.cfg
        sx = _shape(rx, c.deadzone) * (-1 if c.invert_x else 1)
        sy = _shape(ry, c.deadzone) * (-1 if c.invert_y else 1)
        self.yaw += sx * c.yaw_speed * c.sens * dt
        self.pitch = min(c.max_pitch, max(c.min_pitch, self.pitch + sy * c.pitch_speed * c.sens * dt))

        target = tuple(target[:3])
        if self.pivot is None or c.smoothing <= 0:
            self.pivot = target
        else:
            k = 1.0 - math.exp(-c.smoothing * dt)
            self.pivot = tuple(p + (t - p) * k for p, t in zip(self.pivot, target))

        cp = math.cos(self.pitch)
        px, py, pz = self.pivot
        eye = (
            px + c.distance * cp * math.sin(self.yaw),
            py - c.distance * math.sin(self.pitch) - c.lift,
            pz + c.distance * cp * math.cos(self.yaw),
        )
        return eye, self.pivot
