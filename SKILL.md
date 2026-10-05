---
name: lightweight-computer-use
description: This skill should be used when an AI agent must operate a native Windows or macOS GUI through a lightweight screenshot-and-input loop: inspect the screen, translate UI targets into resolution-independent coordinates, execute mouse/keyboard/scroll actions, capture the result, and retry after visual verification. Prefer browser automation, accessibility APIs, or product APIs when available; use this skill for native apps or otherwise inaccessible interfaces.
agent_created: true
---

# Lightweight Computer Use

## Purpose

Drive native Windows and macOS interfaces with a small, auditable Python runner. Keep visual reasoning in the calling AI agent and keep deterministic screen capture, coordinate conversion, input events, and evidence collection in `scripts/desktop_automation.py`.

## One-Time Setup

Resolve `SKILL_ROOT` to the directory containing this file. Use Python 3.10 or later. Create an isolated virtual environment outside the packaged files, then install `requirements.txt`. The examples below assume the default user-level installation path; replace it when the Skill is installed elsewhere.

Windows PowerShell:

```powershell
py -3 -m venv "$env:USERPROFILE\.workbuddy\venvs\lightweight-computer-use"
& "$env:USERPROFILE\.workbuddy\venvs\lightweight-computer-use\Scripts\python.exe" -m pip install -r "$env:USERPROFILE\.workbuddy\skills\lightweight-computer-use\requirements.txt"
```

macOS:

```bash
python3 -m venv "$HOME/.workbuddy/venvs/lightweight-computer-use"
"$HOME/.workbuddy/venvs/lightweight-computer-use/bin/python" -m pip install -r "$HOME/.workbuddy/skills/lightweight-computer-use/requirements.txt"
```

On macOS, grant Screen Recording and Accessibility permission to the terminal or WorkBuddy host that launches Python. On Windows, use a normal interactive desktop session. The runner attempts per-monitor DPI awareness and blocks non-primary execution when the current scaled multi-monitor layout cannot be mapped safely.

## Closed-Loop Workflow

### 1. Inspect the runtime

```bash
python scripts/desktop_automation.py doctor
```

Confirm that capture and input backends load, note monitor IDs and runtime scale, and resolve any permission issue before continuing.

### 2. Capture the current state

```bash
python scripts/desktop_automation.py capture --monitor 1 --output <run-dir> --label initial
```

Read both returned paths: the PNG is visual evidence and the JSON file records the exact capture bounds. Always preserve the metadata file with the screenshot.

### 3. Design the action path

Analyze the screenshot and decompose the goal into the smallest reliable sequence using only:

- `move`, `click`, `double_click`, `right_click`
- `scroll`
- `type`, `press`, `hotkey`
- `wait`, `wait_for_change`, `screenshot`

Prefer `normalized` targets, where `x` and `y` are in `[0, 1]` relative to a monitor. Use `screenshot` targets only when referencing pixels from the exact captured image and metadata. Avoid `absolute` targets because they are not portable.

For every positional action, include a concise `description` and a model confidence. Split click-and-type into separate actions so failures are diagnosable. See `references/api_reference.md` and `assets/example_asset.txt` for the complete contract.

### 4. Validate and annotate before execution

```bash
python scripts/desktop_automation.py validate --plan <plan.json>
python scripts/desktop_automation.py annotate --plan <plan.json> --capture <initial.capture.json> --output <annotated.png>
```

Inspect the annotated image. Confirm that every marker lands inside the intended control, not merely near it. Revise low-confidence or ambiguous nodes before continuing.

### 5. Execute the path

```bash
python scripts/desktop_automation.py run --plan <plan.json> --artifacts <run-dir>
python scripts/desktop_automation.py run --plan <plan.json> --artifacts <run-dir> --execute --approve-sensitive --step-screenshots
```

Run without `--execute` first to obtain a dry-run report with all resolved input coordinates. A three-second countdown is applied by default; change it with `--countdown`. The runner requires `--approve-sensitive` for pointer, keyboard, and scroll actions. Keep hands off the mouse and keyboard during execution except to move the pointer to the primary display's top-left failsafe corner.

### 6. Verify and repair

Open the final screenshot path from the run report and compare the visible result with the original goal and the plan's `success_criteria`.

- If the state matches, return the final screenshot and a short execution summary.
- If it does not match, capture the current state, explain the mismatch, create a minimal repair plan, annotate it, and execute it.
- Reuse current screenshots rather than stale coordinates.
- Stop after 3 unsuccessful loops and return the evidence plus the blocking state.

## Coordinate Policy

Treat captured image pixels and input coordinates as different spaces:

- `normalized`: portable fractions of the selected monitor; preferred across machines and resolutions.
- `screenshot`: pixels from a named capture; convert using its saved bounds and image dimensions.
- `absolute`: raw desktop coordinates; permitted with the separate CLI flag `--allow-absolute`.

The runner requests Windows per-monitor DPI awareness before loading the automation backend, checks the effective DPI mode, and reports the scale between the primary capture and input surfaces. It converts scaled primary-monitor coordinates and fails closed for all secondary-monitor input. To automate another display, make it the OS primary display, rerun `doctor`, and take a fresh screenshot. Annotated markers include the resolved input coordinate.

## Failure Handling

- On a macOS screen-capture or accessibility denial, grant Screen Recording and Accessibility, restart the host application, and rerun `doctor`.
- On `bounds_error`, `coordinate_error`, or `layout_changed`, recapture after the display change and regenerate coordinates.
- On `stale_capture`, take a fresh screenshot. Use `--allow-stale-capture` for diagnostics.
- On `failsafe`, inspect the screen and create a fresh plan.
- On partial execution, trust the latest screenshot rather than the remaining old path.

## Window Preflight

Desktop windows overlap and steal clicks. Before any click plan, minimize or move every window that overlaps the target, bring the target window to the foreground, maximize it, and take a fresh screenshot. Helpers:

- `scripts/focus_window.py <title-substring>` - force a window to the foreground via Win32 (`SwitchToThisWindow` + Alt trick).
- `preflight.py` pattern (see E:/Agents/outputs/mock-task/preflight.py): minimize all windows except the target, activate + maximize the target, capture fresh state.

Text input on pages with a Chinese IME: use clipboard paste (`method: "clipboard"` in a plan, or pyperclip + ctrl+v) instead of `pyautogui.write`.

## CAPTCHA tools

- Character/number captchas: `scripts/captcha/ocr_fill.py read --x N --y N --w N --h N` captures and OCRs the region; `ocr_fill.py fill --text "..." [--click-x N --click-y N]` pastes the text into the focused input via clipboard.
- Slider/puzzle captchas: `scripts/captcha/gap_detect.py --bg-x N --bg-y N --bg-w N --bg-h N --handle-x N --handle-y N` locates the gap and prints drag distance as JSON; `scripts/captcha/slider_drag.py --handle-x N --handle-y N --distance N --style smooth|stepped [--expect-title "..."]` performs a variable-speed drag with a 40px bite check.
