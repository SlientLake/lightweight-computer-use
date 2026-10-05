#!/usr/bin/env python3
"""Lightweight, cross-platform screenshot and GUI action runner.

The calling AI agent performs visual reasoning. This script provides deterministic
screen capture, coordinate mapping, plan validation, input execution, and evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLAN_VERSION = "1.0"
MAX_ACTIONS = 50
SUPPORTED_ACTIONS = {
    "move",
    "click",
    "double_click",
    "right_click",
    "scroll",
    "type",
    "press",
    "hotkey",
    "wait",
    "wait_for_change",
    "screenshot",
}
POSITIONAL_ACTIONS = {
    "move",
    "click",
    "double_click",
    "right_click",
}
BUTTONS = {"left", "middle", "right"}
# 动作类型 -> 默认后置等待（毫秒）。仅对"会改变 UI 状态"的动作给非零默认值。
DEFAULT_PAUSE_MS_BY_TYPE = {
    "move": 0,
    "click": 120,
    "double_click": 120,
    "right_click": 120,
    "scroll": 150,
    "type": 0,
    "press": 80,
    "hotkey": 80,
    "wait": 0,
    "wait_for_change": 0,
    "screenshot": 0,
}


class AutomationError(Exception):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    cleaned = cleaned.strip("-")
    return cleaned[:80] or "capture"


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AutomationError("file_error", f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AutomationError("validation_error", f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AutomationError("validation_error", f"JSON root must be an object: {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def set_windows_dpi_awareness() -> dict[str, Any]:
    if platform.system() != "Windows":
        return {
            "status": "not_applicable",
            "awareness": "platform_managed",
            "safe_global_coordinates": True,
        }
    try:
        import ctypes

        user32 = ctypes.windll.user32
        status = "unchanged"
        errors: list[str] = []

        try:
            set_context = user32.SetProcessDpiAwarenessContext
            set_context.argtypes = [ctypes.c_void_p]
            set_context.restype = ctypes.c_bool
            if set_context(ctypes.c_void_p(-4)):
                status = "per_monitor_aware_v2"
            else:
                errors.append(f"SetProcessDpiAwarenessContext failed with WinError {int(ctypes.windll.kernel32.GetLastError())}")
        except (AttributeError, OSError) as exc:
            errors.append(f"SetProcessDpiAwarenessContext unavailable: {exc}")

        if status == "unchanged":
            try:
                set_awareness = ctypes.windll.shcore.SetProcessDpiAwareness
                set_awareness.argtypes = [ctypes.c_int]
                set_awareness.restype = ctypes.c_long
                result = int(set_awareness(2))
                if result == 0:
                    status = "per_monitor_aware_v1"
                else:
                    errors.append(f"SetProcessDpiAwareness returned HRESULT 0x{result & 0xFFFFFFFF:08X}")
            except (AttributeError, OSError) as exc:
                errors.append(f"SetProcessDpiAwareness unavailable: {exc}")

        awareness = "unknown"
        try:
            get_context = user32.GetThreadDpiAwarenessContext
            get_context.restype = ctypes.c_void_p
            get_awareness = user32.GetAwarenessFromDpiAwarenessContext
            get_awareness.argtypes = [ctypes.c_void_p]
            get_awareness.restype = ctypes.c_int
            awareness_value = int(get_awareness(get_context()))
            awareness = {0: "unaware", 1: "system", 2: "per_monitor"}.get(
                awareness_value, f"unknown_{awareness_value}"
            )
        except (AttributeError, OSError) as exc:
            errors.append(f"Unable to query current DPI awareness: {exc}")

        return {
            "status": status,
            "awareness": awareness,
            "safe_global_coordinates": awareness == "per_monitor",
            "errors": errors,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "awareness": "unknown",
            "safe_global_coordinates": False,
            "errors": [str(exc)],
        }


def load_backends() -> tuple[Any, Any, Any, Any]:
    current_platform = platform.system()
    if current_platform not in {"Windows", "Darwin"}:
        raise AutomationError("platform_error", "Only Windows and macOS are supported")
    dpi_info = set_windows_dpi_awareness()
    try:
        import mss
        import pyautogui
        import pyperclip
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:
        raise AutomationError(
            "dependency_error",
            "Unable to load GUI dependencies. Install requirements.txt in an isolated "
            f"virtual environment. Original error: {exc}",
        ) from exc

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
    pillow_bundle = (Image, ImageDraw, ImageFont)
    return mss, pyautogui, pyperclip, {"pillow": pillow_bundle, "dpi_info": dpi_info}


def open_capture_backend(mss_module: Any) -> Any:
    factory = getattr(mss_module, "mss", None)
    legacy = getattr(mss_module, "MSS", None)
    constructor = legacy if callable(legacy) else factory
    if not callable(constructor):
        raise AutomationError("dependency_error", "Unable to locate an mss capture constructor")
    return constructor()


def capture_monitors(mss_module: Any) -> list[dict[str, int]]:
    with open_capture_backend(mss_module) as sct:
        return [
            {
                "id": index,
                "left": int(item["left"]),
                "top": int(item["top"]),
                "width": int(item["width"]),
                "height": int(item["height"]),
            }
            for index, item in enumerate(sct.monitors)
        ]


def get_screen_info(mss_module: Any, pyautogui: Any, dpi_info: dict[str, Any]) -> dict[str, Any]:
    monitors = capture_monitors(mss_module)
    if len(monitors) < 2:
        raise AutomationError("screen_error", "No physical monitor was reported by the capture backend")

    input_size = pyautogui.size()
    primary = monitors[1]
    scale_x = float(input_size.width) / float(primary["width"])
    scale_y = float(input_size.height) / float(primary["height"])
    unit_scale = math.isclose(scale_x, 1.0, rel_tol=0.02) and math.isclose(scale_y, 1.0, rel_tol=0.02)
    warnings: list[str] = []

    if not unit_scale:
        warnings.append(
            "Capture and input surfaces use different scales. Primary-monitor coordinates will "
            "be converted, while non-primary execution is blocked."
        )

    current_platform = platform.system()
    if current_platform == "Windows" and not dpi_info.get("safe_global_coordinates", False):
        warnings.append(
            "Windows is not confirmed per-monitor DPI aware. Non-primary monitor execution is blocked."
        )

    non_primary_allowed = False
    if len(monitors) > 2:
        warnings.append(
            "This lightweight runner executes only on monitor 1. Set the target display as the "
            "primary display before building the plan. Secondary-monitor capture remains read-only."
        )

    return {
        "platform": current_platform,
        "platform_release": platform.release(),
        "dpi_awareness": dpi_info,
        "monitors": monitors,
        "input_primary_size": {"width": int(input_size.width), "height": int(input_size.height)},
        "capture_to_input_scale": {"x": scale_x, "y": scale_y},
        "coordinate_policy": {
            "primary_monitor": 1,
            "non_primary_allowed": non_primary_allowed,
            "unit_scale": unit_scale,
        },
        "warnings": warnings,
    }


def monitor_by_id(screen: dict[str, Any], monitor_id: int) -> dict[str, int]:
    for item in screen["monitors"]:
        if item["id"] == monitor_id:
            return item
    raise AutomationError("bounds_error", f"Monitor id {monitor_id} is not available")


def capture_screen(
    mss_module: Any,
    pillow_bundle: tuple[Any, Any, Any],
    output_dir: Path,
    label: str,
    monitor_id: int,
    screen: dict[str, Any],
) -> dict[str, Any]:
    Image, _, _ = pillow_bundle
    monitor = monitor_by_id(screen, monitor_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stem = f"{stamp}-{safe_label(label)}"
    png_path = (output_dir / f"{stem}.png").resolve()
    meta_path = (output_dir / f"{stem}.capture.json").resolve()

    with open_capture_backend(mss_module) as sct:
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        image.save(png_path)

    metadata = {
        "version": "1.0",
        "created_at": utc_now(),
        "platform": screen["platform"],
        "monitor_id": monitor_id,
        "desktop_bounds": {
            "left": monitor["left"],
            "top": monitor["top"],
            "width": monitor["width"],
            "height": monitor["height"],
        },
        "image": {
            "path": str(png_path),
            "width": int(image.width),
            "height": int(image.height),
        },
        "screen": screen,
    }
    write_json(meta_path, metadata)
    return {"image_path": str(png_path), "metadata_path": str(meta_path), "metadata": metadata}


def require_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AutomationError("validation_error", f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise AutomationError("validation_error", f"{name} must be finite")
    return result


def validate_target(target: Any, allow_absolute: bool, index: int) -> list[str]:
    if not isinstance(target, dict):
        raise AutomationError("validation_error", f"actions[{index}].target must be an object")
    space = target.get("space")
    if space not in {"normalized", "screenshot", "absolute"}:
        raise AutomationError(
            "validation_error",
            f"actions[{index}].target.space must be normalized, screenshot, or absolute",
        )
    x = require_number(target.get("x"), f"actions[{index}].target.x")
    y = require_number(target.get("y"), f"actions[{index}].target.y")
    warnings: list[str] = []
    if space == "normalized" and not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise AutomationError("validation_error", f"actions[{index}] normalized target is outside [0, 1]")
    if space == "normalized" and "monitor" in target:
        monitor_value = target["monitor"]
        if isinstance(monitor_value, bool) or not isinstance(monitor_value, int) or monitor_value < 0:
            raise AutomationError("validation_error", f"actions[{index}].target.monitor must be a non-negative integer")
    if space == "screenshot" and (x < 0 or y < 0):
        raise AutomationError("validation_error", f"actions[{index}] screenshot target cannot be negative")
    if space == "absolute":
        if not allow_absolute:
            raise AutomationError(
                "validation_error",
                f"actions[{index}] uses absolute coordinates; pass --allow-absolute explicitly",
            )
        warnings.append(f"actions[{index}] uses fragile absolute coordinates")
    return warnings


def action_requires_approval(action: dict[str, Any]) -> bool:
    return bool(action.get("sensitive", False)) or action.get("type") in {
        "move",
        "click",
        "double_click",
        "right_click",
        "scroll",
        "type",
        "press",
        "hotkey",
    }


def validate_plan(
    plan: dict[str, Any],
    capture_meta: dict[str, Any] | None = None,
    allow_absolute: bool = False,
) -> list[str]:
    if plan.get("version") != PLAN_VERSION:
        raise AutomationError("validation_error", f"version must be {PLAN_VERSION}")
    if not isinstance(plan.get("goal"), str) or not plan["goal"].strip():
        raise AutomationError("validation_error", "goal must be a non-empty string")
    criteria = plan.get("success_criteria", [])
    if criteria and (not isinstance(criteria, list) or not all(isinstance(x, str) and x.strip() for x in criteria)):
        raise AutomationError("validation_error", "success_criteria must be a list of non-empty strings")

    monitor = plan.get("monitor", 1)
    if isinstance(monitor, bool) or not isinstance(monitor, int) or monitor < 0:
        raise AutomationError("validation_error", "monitor must be a non-negative integer")

    pause = require_number(plan.get("default_pause_ms", 350), "default_pause_ms")
    if not 0 <= pause <= 5000:
        raise AutomationError("validation_error", "default_pause_ms must be between 0 and 5000")
    runtime = require_number(plan.get("max_runtime_seconds", 120), "max_runtime_seconds")
    if not 1 <= runtime <= 600:
        raise AutomationError("validation_error", "max_runtime_seconds must be between 1 and 600")

    actions = plan.get("actions")
    if not isinstance(actions, list) or not actions:
        raise AutomationError("validation_error", "actions must be a non-empty list")
    if len(actions) > MAX_ACTIONS:
        raise AutomationError("validation_error", f"actions exceeds the maximum of {MAX_ACTIONS}")

    seen_ids: set[str] = set()
    warnings: list[str] = []
    screenshot_target_seen = False
    if plan.get("allow_absolute"):
        warnings.append("Plan-level allow_absolute does not authorize absolute coordinates; use the CLI flag")

    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise AutomationError("validation_error", f"actions[{index}] must be an object")
        action_id = action.get("id")
        if not isinstance(action_id, str) or not action_id.strip():
            raise AutomationError("validation_error", f"actions[{index}].id must be a non-empty string")
        if action_id in seen_ids:
            raise AutomationError("validation_error", f"Duplicate action id: {action_id}")
        seen_ids.add(action_id)

        action_type = action.get("type")
        if action_type not in SUPPORTED_ACTIONS:
            raise AutomationError("validation_error", f"Unsupported action type at actions[{index}]: {action_type}")
        if not isinstance(action.get("description"), str) or not action["description"].strip():
            warnings.append(f"actions[{index}] has no useful description")
        confidence = require_number(action.get("confidence", 0.0), f"actions[{index}].confidence")
        if not 0.0 <= confidence <= 1.0:
            raise AutomationError("validation_error", f"actions[{index}].confidence must be in [0, 1]")
        if confidence < 0.75:
            warnings.append(f"actions[{index}] has low coordinate confidence: {confidence:.2f}")
        if not isinstance(action.get("sensitive", False), bool):
            raise AutomationError("validation_error", f"actions[{index}].sensitive must be boolean")

        if action_type in POSITIONAL_ACTIONS:
            warnings.extend(validate_target(action.get("target"), allow_absolute, index))
        elif action_type == "scroll" and "target" in action:
            warnings.extend(validate_target(action["target"], allow_absolute, index))
        elif "target" in action:
            raise AutomationError(
                "validation_error",
                f"actions[{index}] type {action_type} cannot contain target; click first in a separate action",
            )
        if isinstance(action.get("target"), dict) and action["target"].get("space") == "screenshot":
            screenshot_target_seen = True

        if action_type in {"click", "double_click", "right_click"}:
            button = "right" if action_type == "right_click" else action.get("button", "left")
            if button not in BUTTONS:
                raise AutomationError("validation_error", f"actions[{index}].button is invalid")
            if action_type == "right_click" and action.get("button", "right") != "right":
                raise AutomationError("validation_error", f"actions[{index}] right_click must use the right button")
            clicks = action.get("clicks", 2 if action_type == "double_click" else 1)
            if isinstance(clicks, bool) or not isinstance(clicks, int) or not 1 <= clicks <= 3:
                raise AutomationError("validation_error", f"actions[{index}].clicks must be between 1 and 3")
            click_interval = require_number(action.get("click_interval_ms", 120), f"actions[{index}].click_interval_ms")
            if not 0 <= click_interval <= 1000:
                raise AutomationError("validation_error", f"actions[{index}].click_interval_ms must be between 0 and 1000")
        elif action_type == "scroll":
            amount = action.get("amount")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0 or abs(amount) > 100:
                raise AutomationError("validation_error", f"actions[{index}].amount must be a non-zero integer within [-100, 100]")
        elif action_type == "type":
            if not isinstance(action.get("text"), str):
                raise AutomationError("validation_error", f"actions[{index}].text must be a string")
            if len(action["text"]) > 10000:
                raise AutomationError("validation_error", f"actions[{index}].text exceeds 10000 characters")
            if action.get("method", "keys") not in {"keys", "clipboard"}:
                raise AutomationError("validation_error", f"actions[{index}].method must be keys or clipboard")
        elif action_type == "press":
            if not isinstance(action.get("key"), str) or not action["key"]:
                raise AutomationError("validation_error", f"actions[{index}].key must be a non-empty string")
            presses = action.get("presses", 1)
            if isinstance(presses, bool) or not isinstance(presses, int) or not 1 <= presses <= 20:
                raise AutomationError("validation_error", f"actions[{index}].presses must be between 1 and 20")
        elif action_type == "hotkey":
            keys = action.get("keys")
            if not isinstance(keys, list) or not keys or len(keys) > 5 or not all(isinstance(k, str) and k for k in keys):
                raise AutomationError("validation_error", f"actions[{index}].keys must contain 1 to 5 non-empty strings")
        elif action_type == "wait":
            seconds = require_number(action.get("seconds"), f"actions[{index}].seconds")
            if not 0 <= seconds <= 30:
                raise AutomationError("validation_error", f"actions[{index}].seconds must be between 0 and 30")
        elif action_type == "wait_for_change":
            timeout_seconds = require_number(action.get("timeout_seconds", 5.0), f"actions[{index}].timeout_seconds")
            if not 0.1 <= timeout_seconds <= 30:
                raise AutomationError("validation_error", f"actions[{index}].timeout_seconds must be between 0.1 and 30")
            if "monitor" in action:
                monitor_value = action["monitor"]
                if isinstance(monitor_value, bool) or not isinstance(monitor_value, int) or monitor_value < 0:
                    raise AutomationError("validation_error", f"actions[{index}].monitor must be a non-negative integer")
        elif action_type == "screenshot" and not isinstance(action.get("label", action_id), str):
            raise AutomationError("validation_error", f"actions[{index}].label must be a string")

        if action_type in {"move", "scroll"}:
            duration = require_number(action.get("duration_ms", 200 if action_type == "move" else 150), f"actions[{index}].duration_ms")
            if not 0 <= duration <= 5000:
                raise AutomationError("validation_error", f"actions[{index}].duration_ms must be between 0 and 5000")
        if action_type in {"type", "press"}:
            interval = require_number(action.get("interval_ms", 20 if action_type == "type" else 80), f"actions[{index}].interval_ms")
            if not 0 <= interval <= 1000:
                raise AutomationError("validation_error", f"actions[{index}].interval_ms must be between 0 and 1000")
        if "pause_after_ms" in action:
            action_pause = require_number(action["pause_after_ms"], f"actions[{index}].pause_after_ms")
            if not 0 <= action_pause <= 5000:
                raise AutomationError("validation_error", f"actions[{index}].pause_after_ms must be between 0 and 5000")

    if screenshot_target_seen and capture_meta is None:
        raise AutomationError(
            "validation_error",
            "At least one screenshot target is present but no capture metadata was supplied",
        )
    if not criteria:
        warnings.append("success_criteria is empty; visual completion will be harder to verify")
    return warnings


def load_capture_for_plan(plan: dict[str, Any], explicit_path: Path | None) -> dict[str, Any] | None:
    if explicit_path is not None:
        return read_json(explicit_path.resolve())
    plan_capture = plan.get("capture")
    if isinstance(plan_capture, str) and plan_capture.strip():
        return read_json(Path(plan_capture).expanduser().resolve())
    return None


def validate_capture_for_execution(
    capture_meta: dict[str, Any] | None,
    screen: dict[str, Any],
    allow_stale: bool,
) -> list[str]:
    if capture_meta is None:
        return []
    warnings: list[str] = []
    created_at = capture_meta.get("created_at")
    if isinstance(created_at, str):
        try:
            captured_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if captured_at.tzinfo is None:
                captured_at = captured_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - captured_at.astimezone(timezone.utc)).total_seconds()
            if age_seconds > 300:
                message = f"Capture is stale ({int(age_seconds)} seconds old); recapture before execution"
                if not allow_stale:
                    raise AutomationError("stale_capture", message)
                warnings.append(message)
        except ValueError as exc:
            raise AutomationError("validation_error", "capture.created_at is not valid ISO 8601") from exc

    capture_monitor = int(capture_meta.get("monitor_id", 1))
    current = monitor_by_id(screen, capture_monitor)
    bounds = capture_meta.get("desktop_bounds")
    if not isinstance(bounds, dict):
        raise AutomationError("validation_error", "capture.desktop_bounds must be an object")
    expected = {key: int(bounds[key]) for key in ("left", "top", "width", "height")}
    actual = {key: int(current[key]) for key in ("left", "top", "width", "height")}
    if expected != actual:
        raise AutomationError(
            "layout_changed",
            "Display layout differs from the source capture; take a new screenshot and rebuild the plan",
        )
    return warnings


def resolve_plan_targets(
    plan: dict[str, Any],
    screen: dict[str, Any],
    capture_meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    default_monitor = int(plan.get("monitor", 1))
    monitor_by_id(screen, default_monitor)
    resolved_actions: list[dict[str, Any]] = []
    for action in plan["actions"]:
        entry: dict[str, Any] = {"id": action["id"], "type": action["type"]}
        if isinstance(action.get("target"), dict):
            entry["resolved_target"] = resolve_target(
                action["target"], screen, default_monitor, capture_meta
            )
        resolved_actions.append(entry)
    return resolved_actions


def point_in_capture_space(
    target: dict[str, Any],
    screen: dict[str, Any],
    default_monitor: int,
    capture_meta: dict[str, Any] | None,
) -> tuple[float, float, int | None]:
    space = target["space"]
    x = float(target["x"])
    y = float(target["y"])

    if space == "normalized":
        monitor_id = int(target.get("monitor", default_monitor))
        monitor = monitor_by_id(screen, monitor_id)
        desktop_x = monitor["left"] + x * max(monitor["width"] - 1, 1)
        desktop_y = monitor["top"] + y * max(monitor["height"] - 1, 1)
        return desktop_x, desktop_y, monitor_id

    if space == "screenshot":
        if capture_meta is None:
            raise AutomationError("validation_error", "Screenshot target requires capture metadata")
        bounds = capture_meta["desktop_bounds"]
        image = capture_meta["image"]
        if x >= float(image["width"]) or y >= float(image["height"]):
            raise AutomationError("bounds_error", "Screenshot target lies outside the captured image")
        desktop_x = float(bounds["left"]) + x * float(bounds["width"]) / float(image["width"])
        desktop_y = float(bounds["top"]) + y * float(bounds["height"]) / float(image["height"])
        return desktop_x, desktop_y, int(capture_meta.get("monitor_id", default_monitor))

    return x, y, None


def to_input_space(
    desktop_x: float,
    desktop_y: float,
    screen: dict[str, Any],
    monitor_id: int | None,
) -> tuple[int, int]:
    policy = screen["coordinate_policy"]
    primary_id = int(policy["primary_monitor"])
    scale = screen["capture_to_input_scale"]

    if monitor_id == primary_id:
        primary = monitor_by_id(screen, primary_id)
        input_x = int(round((desktop_x - primary["left"]) * float(scale["x"])))
        input_y = int(round((desktop_y - primary["top"]) * float(scale["y"])))
        return input_x, input_y

    if monitor_id is None:
        if not policy["unit_scale"] or not policy["non_primary_allowed"]:
            raise AutomationError(
                "coordinate_error",
                "Absolute coordinates are unsafe on the current scaled or non-per-monitor-aware layout",
            )
        return int(round(desktop_x)), int(round(desktop_y))

    if not policy["non_primary_allowed"]:
        raise AutomationError(
            "coordinate_error",
            f"Monitor {monitor_id} is blocked because the current multi-monitor scale cannot be mapped safely",
        )
    return int(round(desktop_x)), int(round(desktop_y))


def resolve_target(
    target: dict[str, Any],
    screen: dict[str, Any],
    default_monitor: int,
    capture_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    desktop_x, desktop_y, monitor_id = point_in_capture_space(target, screen, default_monitor, capture_meta)
    virtual = monitor_by_id(screen, 0)
    if not (
        virtual["left"] <= desktop_x < virtual["left"] + virtual["width"]
        and virtual["top"] <= desktop_y < virtual["top"] + virtual["height"]
    ):
        raise AutomationError("bounds_error", f"Resolved target ({desktop_x}, {desktop_y}) is outside the virtual desktop")
    if monitor_id is None:
        for monitor in screen["monitors"]:
            if monitor["id"] == 0:
                continue
            if (
                monitor["left"] <= desktop_x < monitor["left"] + monitor["width"]
                and monitor["top"] <= desktop_y < monitor["top"] + monitor["height"]
            ):
                monitor_id = int(monitor["id"])
                break
    input_x, input_y = to_input_space(desktop_x, desktop_y, screen, monitor_id)
    return {
        "desktop_x": desktop_x,
        "desktop_y": desktop_y,
        "input_x": input_x,
        "input_y": input_y,
        "monitor": monitor_id,
        "space": target["space"],
    }


def annotate_plan(
    plan: dict[str, Any],
    capture_meta: dict[str, Any],
    current_screen: dict[str, Any],
    output_path: Path,
    pillow_bundle: tuple[Any, Any, Any],
) -> dict[str, Any]:
    Image, ImageDraw, ImageFont = pillow_bundle
    image_path = Path(capture_meta["image"]["path"])
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    bounds = capture_meta["desktop_bounds"]
    warnings: list[str] = []
    marker_count = 0

    capture_screen_info = current_screen

    for index, action in enumerate(plan["actions"], start=1):
        target = action.get("target")
        if not isinstance(target, dict):
            continue
        resolved = resolve_target(
            target,
            capture_screen_info,
            int(plan.get("monitor", 1)),
            capture_meta,
        )
        desktop_x = float(resolved["desktop_x"])
        desktop_y = float(resolved["desktop_y"])
        px = (desktop_x - float(bounds["left"])) * image.width / float(bounds["width"])
        py = (desktop_y - float(bounds["top"])) * image.height / float(bounds["height"])
        if not (0 <= px < image.width and 0 <= py < image.height):
            warnings.append(f"Action {action['id']} is outside the annotated capture")
            continue

        marker_count += 1
        radius = max(10, int(min(image.width, image.height) * 0.012))
        color = (220, 38, 38) if float(action.get("confidence", 0.0)) >= 0.75 else (234, 88, 12)
        left = int(px - radius)
        top = int(py - radius)
        right = int(px + radius)
        bottom = int(py + radius)
        draw.ellipse((left, top, right, bottom), outline=color, width=max(2, radius // 5))
        draw.line((int(px - radius), int(py), int(px + radius), int(py)), fill=color, width=2)
        draw.line((int(px), int(py - radius), int(px), int(py + radius)), fill=color, width=2)
        label = f"{index}@{resolved['input_x']},{resolved['input_y']}"
        text_box = draw.textbbox((0, 0), label, font=font)
        text_w = text_box[2] - text_box[0]
        text_h = text_box[3] - text_box[1]
        label_x = max(0, min(image.width - text_w - 8, int(px + radius + 3)))
        label_y = max(0, min(image.height - text_h - 8, int(py - radius - 3)))
        draw.rectangle((label_x, label_y, label_x + text_w + 8, label_y + text_h + 6), fill=(255, 255, 255), outline=color, width=2)
        draw.text((label_x + 4, label_y + 3), label, fill=(17, 24, 39), font=font)

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return {
        "status": "annotated",
        "output_path": str(output_path),
        "markers": marker_count,
        "warnings": warnings,
    }


def configure_failsafe(pyautogui: Any, screen: dict[str, Any]) -> list[tuple[int, int]]:
    points: set[tuple[int, int]] = {(0, 0)}
    for monitor in screen["monitors"]:
        if monitor["id"] == 0:
            continue
        try:
            points.add(to_input_space(float(monitor["left"]), float(monitor["top"]), screen, int(monitor["id"])))
        except AutomationError:
            continue
    result = sorted(points)
    if hasattr(pyautogui, "FAILSAFE_POINTS"):
        pyautogui.FAILSAFE_POINTS = result
    return result


def guarded_sleep(seconds: float, pyautogui: Any) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        pyautogui.failSafeCheck()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def screen_signature(mss_module: Any, monitor: dict[str, int], sample_step: int = 8) -> int:
    """Downsampled pixel signature of a monitor for cheap change detection."""
    with open_capture_backend(mss_module) as sct:
        shot = sct.grab(monitor)
    raw = shot.rgb
    width, height = shot.size
    signature = 14695981039346656037
    for y in range(0, height, sample_step):
        row_base = y * width * 3
        for x in range(0, width, sample_step):
            offset = row_base + x * 3
            pixel = (raw[offset] << 16) | (raw[offset + 1] << 8) | raw[offset + 2]
            signature ^= pixel
            signature = (signature * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return signature


def wait_for_change(
    mss_module: Any,
    pyautogui: Any,
    screen: dict[str, Any],
    monitor_id: int,
    timeout_seconds: float,
    baseline_signature: int | None = None,
) -> dict[str, Any]:
    """Block until the screen differs from the baseline or timeout expires."""
    monitor = monitor_by_id(screen, monitor_id)
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    if baseline_signature is None:
        baseline_signature = screen_signature(mss_module, monitor)
    last = baseline_signature
    checks = 0
    while time.monotonic() < deadline:
        pyautogui.failSafeCheck()
        time.sleep(0.12)
        current = screen_signature(mss_module, monitor)
        checks += 1
        if current != baseline_signature:
            return {
                "changed": True,
                "checks": checks,
                "elapsed_ms": int((time.monotonic() - (deadline - timeout_seconds)) * 1000),
                "baseline": baseline_signature,
                "current": current,
            }
        last = current
    return {
        "changed": False,
        "checks": checks,
        "elapsed_ms": int(timeout_seconds * 1000),
        "baseline": baseline_signature,
        "current": last,
    }


def execute_action(
    action: dict[str, Any],
    pyautogui: Any,
    pyperclip: Any,
    screen: dict[str, Any],
    default_monitor: int,
    capture_meta: dict[str, Any] | None,
    screenshot_callback: Any,
    mss_holder: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action_type = action["type"]
    result: dict[str, Any] = {"id": action["id"], "type": action_type, "status": "completed"}
    resolved: dict[str, Any] | None = None
    if action_type == "wait_for_change" and (mss_holder is None or mss_holder.get("mss_module") is None):
        raise AutomationError("validation_error", "wait_for_change requires the capture backend")

    if isinstance(action.get("target"), dict):
        resolved = resolve_target(action["target"], screen, default_monitor, capture_meta)
        result["resolved_target"] = resolved

    if action_type == "move":
        pyautogui.moveTo(resolved["input_x"], resolved["input_y"], duration=float(action.get("duration_ms", 200)) / 1000.0)
    elif action_type in {"click", "double_click", "right_click"}:
        clicks = int(action.get("clicks", 2 if action_type == "double_click" else 1))
        button = "right" if action_type == "right_click" else action.get("button", "left")
        pyautogui.click(
            resolved["input_x"],
            resolved["input_y"],
            clicks=clicks,
            interval=float(action.get("click_interval_ms", 120)) / 1000.0,
            button=button,
        )
    elif action_type == "scroll":
        if resolved is not None:
            pyautogui.moveTo(resolved["input_x"], resolved["input_y"], duration=float(action.get("duration_ms", 150)) / 1000.0)
        pyautogui.scroll(int(action["amount"]))
    elif action_type == "type":
        text = action["text"]
        method = action.get("method", "keys")
        if method == "keys":
            if not text.isascii():
                raise AutomationError(
                    "action_error",
                    f"Action {action['id']} contains non-ASCII text; use method=clipboard",
                )
            pyautogui.write(text, interval=float(action.get("interval_ms", 20)) / 1000.0)
        else:
            previous_clipboard: str | None = None
            try:
                previous_clipboard = pyperclip.paste()
            except Exception:
                previous_clipboard = None
            try:
                pyperclip.copy(text)
                paste_modifier = "command" if platform.system() == "Darwin" else "ctrl"
                pyautogui.hotkey(paste_modifier, "v")
                guarded_sleep(0.15, pyautogui)
            finally:
                if previous_clipboard is not None:
                    try:
                        pyperclip.copy(previous_clipboard)
                    except Exception:
                        pass
        result["text_length"] = len(text)
    elif action_type == "press":
        pyautogui.press(action["key"], presses=int(action.get("presses", 1)), interval=float(action.get("interval_ms", 80)) / 1000.0)
    elif action_type == "hotkey":
        pyautogui.hotkey(*action["keys"])
    elif action_type == "wait":
        guarded_sleep(float(action["seconds"]), pyautogui)
    elif action_type == "wait_for_change":
        result["wait"] = wait_for_change(
            mss_holder["mss_module"],
            pyautogui,
            screen,
            int(action.get("monitor", default_monitor)),
            float(action.get("timeout_seconds", 5.0)),
        )
    elif action_type == "screenshot":
        result["capture"] = screenshot_callback(action.get("label", action["id"]))

    return result


def cmd_doctor(_: argparse.Namespace) -> int:
    mss_module, pyautogui, _, backend = load_backends()
    screen = get_screen_info(mss_module, pyautogui, backend["dpi_info"])
    try:
        with open_capture_backend(mss_module) as sct:
            probe = sct.grab(sct.monitors[1])
            capture_probe = {"status": "ok", "width": int(probe.width), "height": int(probe.height)}
    except Exception as exc:
        capture_probe = {"status": "failed", "message": str(exc)}
    print(json.dumps({"status": "ok", "capture_probe": capture_probe, "screen": screen}, ensure_ascii=False, indent=2))
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    mss_module, pyautogui, _, backend = load_backends()
    screen = get_screen_info(mss_module, pyautogui, backend["dpi_info"])
    result = capture_screen(
        mss_module,
        backend["pillow"],
        Path(args.output).expanduser().resolve(),
        args.label,
        args.monitor,
        screen,
    )
    print(json.dumps({"status": "captured", **{k: v for k, v in result.items() if k != "metadata"}}, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).expanduser().resolve()
    plan = read_json(plan_path)
    capture_meta = load_capture_for_plan(plan, Path(args.capture).expanduser() if args.capture else None)
    warnings = validate_plan(plan, capture_meta, allow_absolute=args.allow_absolute)
    approval_actions = [a["id"] for a in plan["actions"] if action_requires_approval(a)]
    summary = {
        "status": "valid",
        "plan": str(plan_path),
        "actions": len(plan["actions"]),
        "approval_required_actions": approval_actions,
        "declared_sensitive_actions": [a["id"] for a in plan["actions"] if a.get("sensitive", False)],
        "warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_annotate(args: argparse.Namespace) -> int:
    plan = read_json(Path(args.plan).expanduser().resolve())
    capture_meta = load_capture_for_plan(plan, Path(args.capture).expanduser().resolve())
    if capture_meta is None:
        raise AutomationError("validation_error", "annotate requires capture metadata")
    warnings = validate_plan(plan, capture_meta, allow_absolute=args.allow_absolute)
    mss_module, pyautogui, _, backend = load_backends()
    screen = get_screen_info(mss_module, pyautogui, backend["dpi_info"])
    warnings.extend(screen["warnings"])
    warnings.extend(validate_capture_for_execution(capture_meta, screen, allow_stale=False))
    result = annotate_plan(
        plan,
        capture_meta,
        screen,
        Path(args.output).expanduser(),
        backend["pillow"],
    )
    result["warnings"] = warnings + result["warnings"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).expanduser().resolve()
    plan = read_json(plan_path)
    if not 0 <= float(args.countdown) <= 30:
        raise AutomationError("validation_error", "--countdown must be between 0 and 30 seconds")
    capture_meta = load_capture_for_plan(plan, Path(args.capture).expanduser().resolve() if args.capture else None)
    warnings = validate_plan(plan, capture_meta, allow_absolute=args.allow_absolute)
    approval_actions = [action["id"] for action in plan["actions"] if action_requires_approval(action)]

    mss_module, pyautogui, pyperclip, backend = load_backends()
    screen = get_screen_info(mss_module, pyautogui, backend["dpi_info"])
    warnings.extend(screen["warnings"])
    warnings.extend(
        validate_capture_for_execution(
            capture_meta,
            screen,
            allow_stale=args.allow_stale_capture or not args.execute,
        )
    )
    resolved_actions = resolve_plan_targets(plan, screen, capture_meta)

    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "plan": str(plan_path),
                    "actions_total": len(plan["actions"]),
                    "approval_required_actions": approval_actions,
                    "execute_required": True,
                    "resolved_actions": resolved_actions,
                    "warnings": warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if approval_actions and not args.approve_sensitive:
        raise AutomationError(
            "confirmation_required",
            "The plan contains UI input actions. Obtain explicit user approval for the reviewed plan, "
            "then rerun with --approve-sensitive. "
            f"Actions: {', '.join(approval_actions)}",
        )

    default_monitor = int(plan.get("monitor", 1))
    monitor_by_id(screen, default_monitor)
    failsafe_points = configure_failsafe(pyautogui, screen)

    base_dir = Path(args.artifacts).expanduser().resolve()
    run_id = datetime.now().strftime("run-%Y%m%d-%H%M%S-%f") + f"-{os.getpid()}"
    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    def take_capture(label: str) -> dict[str, str]:
        result = capture_screen(
            mss_module,
            backend["pillow"],
            run_dir,
            label,
            default_monitor,
            screen,
        )
        return {"image_path": result["image_path"], "metadata_path": result["metadata_path"]}

    started = time.monotonic()
    step_results: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "status": "running",
        "plan": str(plan_path),
        "started_at": utc_now(),
        "actions_total": len(plan["actions"]),
        "actions_completed": 0,
        "artifacts_dir": str(run_dir),
        "failsafe_points": failsafe_points,
        "step_results": step_results,
        "warnings": warnings,
    }

    try:
        if args.countdown > 0:
            guarded_sleep(args.countdown, pyautogui)
        for index, action in enumerate(plan["actions"], start=1):
            if time.monotonic() - started > float(plan.get("max_runtime_seconds", 120)):
                raise AutomationError("timeout", "Plan exceeded max_runtime_seconds")
            if args.step_screenshots:
                before = take_capture(f"before-{index:02d}-{action['id']}")
            else:
                before = None
            result = execute_action(
                action,
                pyautogui,
                pyperclip,
                screen,
                default_monitor,
                capture_meta,
                take_capture,
                mss_holder={"mss_module": mss_module},
            )
            if before is not None:
                result["before_capture"] = before
            step_results.append(result)
            report["actions_completed"] = index
            pause_default = DEFAULT_PAUSE_MS_BY_TYPE.get(action["type"], 120)
            pause_ms = float(action.get("pause_after_ms", plan.get("default_pause_ms", pause_default)))
            if pause_ms > 0:
                guarded_sleep(pause_ms / 1000.0, pyautogui)

        report["final_capture"] = take_capture("final")
        report["status"] = "completed"
        report["finished_at"] = utc_now()
        write_json(run_dir / "run-report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        try:
            report["failure_capture"] = take_capture("failure")
        except Exception as capture_exc:
            report["failure_capture_error"] = str(capture_exc)
        report["status"] = "failed"
        report["finished_at"] = utc_now()
        if isinstance(exc, AutomationError):
            report["error_type"] = exc.error_type
        elif isinstance(exc, pyautogui.FailSafeException):
            report["error_type"] = "failsafe"
        else:
            report["error_type"] = type(exc).__name__
        report["message"] = str(exc)
        write_json(run_dir / "run-report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3


def cmd_self_test(_: argparse.Namespace) -> int:
    sample_screen = {
        "monitors": [
            {"id": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
            {"id": 1, "left": 0, "top": 0, "width": 1920, "height": 1080},
        ],
        "capture_to_input_scale": {"x": 1.0, "y": 1.0},
        "coordinate_policy": {"primary_monitor": 1, "non_primary_allowed": True, "unit_scale": True},
    }
    sample_capture = {
        "monitor_id": 1,
        "desktop_bounds": {"left": 0, "top": 0, "width": 1920, "height": 1080},
        "image": {"path": "unused.png", "width": 960, "height": 540},
    }
    normalized = resolve_target(
        {"space": "normalized", "monitor": 1, "x": 0.5, "y": 0.5},
        sample_screen,
        1,
        sample_capture,
    )
    screenshot = resolve_target(
        {"space": "screenshot", "x": 480, "y": 270},
        sample_screen,
        1,
        sample_capture,
    )
    assert abs(normalized["input_x"] - 960) <= 1
    assert abs(normalized["input_y"] - 540) <= 1
    assert screenshot["input_x"] == 960
    assert screenshot["input_y"] == 540

    scaled_primary = {
        "monitors": [
            {"id": 0, "left": 0, "top": 0, "width": 2880, "height": 1800},
            {"id": 1, "left": 0, "top": 0, "width": 2880, "height": 1800},
        ],
        "capture_to_input_scale": {"x": 0.5, "y": 0.5},
        "coordinate_policy": {"primary_monitor": 1, "non_primary_allowed": False, "unit_scale": False},
    }
    scaled = resolve_target(
        {"space": "normalized", "monitor": 1, "x": 0.5, "y": 0.5},
        scaled_primary,
        1,
        None,
    )
    assert abs(scaled["input_x"] - 720) <= 1
    assert abs(scaled["input_y"] - 450) <= 1

    blocked_screen = {
        "monitors": [
            {"id": 0, "left": 0, "top": 0, "width": 4800, "height": 2160},
            {"id": 1, "left": 0, "top": 0, "width": 3840, "height": 2160},
            {"id": 2, "left": 3840, "top": 0, "width": 960, "height": 1080},
        ],
        "capture_to_input_scale": {"x": 0.5, "y": 0.5},
        "coordinate_policy": {"primary_monitor": 1, "non_primary_allowed": False, "unit_scale": False},
    }
    try:
        resolve_target(
            {"space": "normalized", "monitor": 2, "x": 0.5, "y": 0.5},
            blocked_screen,
            1,
            None,
        )
        raise AssertionError("Unsafe secondary monitor should be blocked")
    except AutomationError as exc:
        assert exc.error_type == "coordinate_error"

    sample_plan = {
        "version": "1.0",
        "goal": "Self-test",
        "success_criteria": ["Validation completes"],
        "monitor": 1,
        "actions": [
            {
                "id": "wait",
                "type": "wait",
                "description": "Wait briefly",
                "confidence": 1.0,
                "sensitive": False,
                "seconds": 0.0,
            }
        ],
    }
    assert validate_plan(sample_plan) == []
    assert action_requires_approval({"type": "click", "sensitive": False})
    assert not action_requires_approval({"type": "wait", "sensitive": False})
    print(json.dumps({"status": "ok", "tests": 10}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lightweight screenshot-driven desktop automation runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Report backend, permission, monitor, and scaling status")
    doctor_parser.set_defaults(func=cmd_doctor)

    capture_parser = subparsers.add_parser("capture", help="Capture a monitor and save coordinate metadata")
    capture_parser.add_argument("--monitor", type=int, default=1)
    capture_parser.add_argument("--output", required=True)
    capture_parser.add_argument("--label", default="capture")
    capture_parser.set_defaults(func=cmd_capture)

    validate_parser = subparsers.add_parser("validate", help="Validate a plan without controlling the UI")
    validate_parser.add_argument("--plan", required=True)
    validate_parser.add_argument("--capture")
    validate_parser.add_argument("--allow-absolute", action="store_true")
    validate_parser.set_defaults(func=cmd_validate)

    annotate_parser = subparsers.add_parser("annotate", help="Draw numbered action targets over a capture")
    annotate_parser.add_argument("--plan", required=True)
    annotate_parser.add_argument("--capture", required=True)
    annotate_parser.add_argument("--output", required=True)
    annotate_parser.add_argument("--allow-absolute", action="store_true")
    annotate_parser.set_defaults(func=cmd_annotate)

    run_parser = subparsers.add_parser("run", help="Dry-run or execute a validated action plan")
    run_parser.add_argument("--plan", required=True)
    run_parser.add_argument("--capture")
    run_parser.add_argument("--artifacts", required=True)
    run_parser.add_argument("--execute", action="store_true")
    run_parser.add_argument("--approve-sensitive", action="store_true")
    run_parser.add_argument("--allow-absolute", action="store_true")
    run_parser.add_argument("--allow-stale-capture", action="store_true")
    run_parser.add_argument("--step-screenshots", action="store_true")
    run_parser.add_argument("--countdown", type=float, default=3.0)
    run_parser.set_defaults(func=cmd_run)

    self_test_parser = subparsers.add_parser("self-test", help="Run dependency-free validation and coordinate tests")
    self_test_parser.set_defaults(func=cmd_self_test)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except AutomationError as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": exc.error_type, "message": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print(
            json.dumps(
                {"status": "error", "error_type": "interrupted", "message": "Interrupted by user"},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
