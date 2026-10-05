#!/usr/bin/env python3
"""Bring a window to the foreground via Win32, bypassing focus-lock."""

from __future__ import annotations

import ctypes
import sys
import time


def force_foreground(title_substring: str) -> int:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible

    matches: list[int] = []

    def on_window(hwnd: int, _lparam: int) -> bool:
        if IsWindowVisible(hwnd):
            length = GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buf, length + 1)
                if title_substring.lower() in buf.value.lower():
                    matches.append(hwnd)
        return True

    EnumWindows(EnumWindowsProc(on_window), 0)
    if not matches:
        print(f"NO_MATCH:{title_substring}")
        return 2

    hwnd = matches[0]
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.3)

    # SwitchToThisWindow: 不受前台锁限制，最蛮力但最可靠
    user32.SwitchToThisWindow(hwnd, True)
    time.sleep(0.4)

    if user32.GetForegroundWindow() != hwnd:
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        attached = False
        if fg_thread != target_thread:
            attached = bool(user32.AttachThreadInput(target_thread, fg_thread, True))
        user32.keybd_event(0x12, 0, 0, 0)  # Alt down
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, 2, 0)  # Alt up
        if attached:
            user32.AttachThreadInput(target_thread, fg_thread, False)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
    time.sleep(1.0)

    ok = user32.GetForegroundWindow() == hwnd
    buf = ctypes.create_unicode_buffer(128)
    user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 128)
    print(f"OK:{hwnd}:fg={ok}:title={buf.value}")
    return 0 if ok else 3


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: focus_window.py <title-substring>")
        raise SystemExit(1)
    raise SystemExit(force_foreground(sys.argv[1]))
