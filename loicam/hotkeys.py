"""Keyboard hotkeys, read globally but only acted on while PCSX2 is the foreground window."""
import ctypes
import ctypes.wintypes as wt
import os
import time

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetForegroundWindow.restype = wt.HWND
_kernel32.OpenProcess.restype = wt.HANDLE

VK = {
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
}

# action -> key; each action is (setting, step)
BINDINGS = {
    "num4": ("sens", -0.1),
    "num6": ("sens", +0.1),
    "num8": ("distance", -50.0),
    "num2": ("distance", +50.0),
    "num7": ("lift", +25.0),
    "num1": ("lift", -25.0),
    "num9": ("focal", -15),     # lower focal = wider FoV
    "num3": ("focal", +15),
    "num5": ("reset", None),
}
HELP = ("numpad 4/6 sensitivity, 8/2 distance closer/farther, 7/1 angle (camera height) "
        "up/down, 9/3 FoV wider/narrower, 5 reset")

_REPEAT_DELAY = 0.35
_REPEAT_RATE = 0.08


def _foreground_exe():
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = _kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(len(buf))
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        _kernel32.CloseHandle(h)


class Hotkeys:
    def __init__(self, target_exe="pcsx2-qt.exe"):
        self.target = target_exe.lower()
        self.held_since = {}
        self.last_fire = {}
        self._fg_checked = 0.0
        self._fg_ok = False

    def _game_focused(self, now):
        if now - self._fg_checked > 0.25:
            self._fg_checked = now
            self._fg_ok = _foreground_exe() == self.target
        return self._fg_ok

    def poll(self):
        """Return the list of (setting, step) actions triggered since the last call."""
        now = time.perf_counter()
        fired = []
        if not self._game_focused(now):
            self.held_since.clear()
            return fired
        for key, action in BINDINGS.items():
            down = _user32.GetAsyncKeyState(VK[key]) & 0x8000
            if not down:
                self.held_since.pop(key, None)
                continue
            if key not in self.held_since:
                self.held_since[key] = now
                self.last_fire[key] = now
                fired.append(action)
            elif (action[0] != "reset" and now - self.held_since[key] > _REPEAT_DELAY
                  and now - self.last_fire[key] > _REPEAT_RATE):
                self.last_fire[key] = now
                fired.append(action)
        return fired
