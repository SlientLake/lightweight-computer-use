#!/usr/bin/env python3
"""Generic slider-captcha drag with bite-check.

Usage:
  python slider_drag.py --handle-x 840 --handle-y 601 --distance 253 [--style ballistic|smooth|stepped]

Exit codes: 0 done, 2 preflight failure.
"""
from __future__ import annotations

import argparse
import ctypes
import random
import time

import pyautogui

pyautogui.FAILSAFE = True


def foreground_title() -> str:
    buf = ctypes.create_unicode_buffer(128)
    ctypes.windll.user32.GetWindowTextW(
        ctypes.windll.user32.GetForegroundWindow(), buf, 128
    )
    return buf.value


def ballistic_drag(handle: tuple[int, int], dx_remaining: float) -> None:
    """Continuously-accelerating drag ('slide to the far right' type).

    Fast start-to-end power curve (validated v4 trajectory): grabs, then
    accelerates hard all the way and slams into the track end with a quick
    release. NOT an ease-out creep.
    """
    base = handle[0] + 40  # bite check already moved the cursor here
    duration = random.uniform(0.42, 0.55)
    power = random.uniform(1.8, 2.1)
    wy = 0.0
    start = time.perf_counter()
    while True:
        t = (time.perf_counter() - start) / duration
        if t >= 1.0:
            break
        wy = max(-1.2, min(1.2, wy + random.uniform(-0.4, 0.4)))
        pyautogui.moveTo(
            int(base + dx_remaining * (t ** power)), int(handle[1] + wy)
        )
        time.sleep(random.uniform(0.006, 0.014))
    pyautogui.moveTo(int(base + dx_remaining), handle[1])
    time.sleep(random.uniform(0.05, 0.12))
    pyautogui.mouseUp()


def smooth_drag(handle: tuple[int, int], dx: int, duration: float) -> None:
    """Continuous ease-out drag with smooth y drift, ~60fps."""
    start = time.perf_counter()
    wy = 0.0
    while True:
        t = (time.perf_counter() - start) / duration
        if t >= 1.0:
            break
        ease = 1 - (1 - t) ** 2.3
        wy = max(-3.0, min(3.0, wy + random.uniform(-1.0, 1.0)))
        pyautogui.moveTo(
            int(round(handle[0] + dx * ease)), int(handle[1] + wy)
        )
        time.sleep(0.012)
    pyautogui.moveTo(handle[0] + dx, handle[1])


def stepped_drag(handle: tuple[int, int], dx: int) -> None:
    """Segmented accelerate/decelerate drag with per-step jitter."""
    pyautogui.moveTo(*handle, duration=0.35)
    time.sleep(random.uniform(0.15, 0.3))
    pyautogui.mouseDown()
    time.sleep(random.uniform(0.08, 0.15))
    travelled = 0
    g1, g2 = dx * 0.55, dx * 0.88
    while travelled < dx:
        remain = dx - travelled
        if travelled < g1:
            step = random.randint(9, 16)
        elif travelled < g2:
            step = random.randint(4, 7)
        else:
            step = random.randint(1, 3)
        step = min(step, remain)
        travelled += step
        pyautogui.moveTo(
            handle[0] + travelled, handle[1] + random.randint(-2, 2), duration=0.01
        )
        time.sleep(random.uniform(0.008, 0.028))
    pyautogui.moveTo(handle[0] + dx + random.randint(2, 5), handle[1], duration=0.05)
    time.sleep(random.uniform(0.1, 0.2))
    pyautogui.moveTo(handle[0] + dx, handle[1], duration=0.08)
    time.sleep(random.uniform(0.15, 0.3))
    pyautogui.mouseUp()


def main() -> int:
    parser = argparse.ArgumentParser(description="Drag a slider captcha handle")
    parser.add_argument("--handle-x", type=int, required=True)
    parser.add_argument("--handle-y", type=int, required=True)
    parser.add_argument("--distance", type=int, required=True)
    parser.add_argument("--style", choices=["ballistic", "smooth", "stepped"], default="ballistic")
    parser.add_argument("--expect-title", default="", help="Abort if this substring leaves the foreground")
    args = parser.parse_args()

    handle = (args.handle_x, args.handle_y)
    if args.expect_title and args.expect_title not in foreground_title():
        print(f"PREFLIGHT_FAIL: target window not in foreground: {foreground_title()}")
        return 2

    pyautogui.moveTo(*handle, duration=0.35)
    time.sleep(random.uniform(0.2, 0.35))
    pyautogui.mouseDown()
    time.sleep(random.uniform(0.09, 0.15))

    # bite check: 40px then verify the handle follows before committing
    smooth_drag_handle = smooth_drag
    start = time.perf_counter()
    wy = 0.0
    while True:
        t = (time.perf_counter() - start) / 0.28
        if t >= 1.0:
            break
        ease = 1 - (1 - t) ** 2.3
        wy = max(-3.0, min(3.0, wy + random.uniform(-1.0, 1.0)))
        pyautogui.moveTo(int(round(handle[0] + 40 * ease)), int(handle[1] + wy))
        time.sleep(0.012)
    time.sleep(0.12)
    if args.expect_title and args.expect_title not in foreground_title():
        pyautogui.mouseUp()
        print("FOCUS_LOST_DURING_DRAG")
        return 2

    if args.style == "ballistic":
        ballistic_drag(handle, args.distance - 40)
    elif args.style == "smooth":
        smooth_drag_handle(handle, args.distance - 40, 0.55)
        time.sleep(random.uniform(0.15, 0.25))
        pyautogui.mouseUp()
    else:
        pyautogui.mouseUp()
        time.sleep(0.4)
        stepped_drag(handle, args.distance)

    time.sleep(2.5)
    if args.expect_title and args.expect_title not in foreground_title():
        print("FOCUS_LOST_AFTER_DRAG")
        return 2
    print("DRAG_DONE - capture a screenshot and verify; if the slider snapped back, treat as risk-control rejection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
