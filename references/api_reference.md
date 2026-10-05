# Action Protocol

## Plan Object

```json
{
  "version": "1.0",
  "goal": "Describe the intended visible outcome",
  "success_criteria": [
    "Describe one visible condition that proves success"
  ],
  "monitor": 1,
  "capture": "C:/absolute/path/initial.capture.json",
  "default_pause_ms": 350,
  "max_runtime_seconds": 120,
  "actions": []
}
```

- `version`: Must be `"1.0"`.
- `goal`: Human-readable objective.
- `success_criteria`: Visible conditions for the Agent's final screenshot check.
- `monitor`: Runtime capture monitor. `1` is the only executable primary display. `0` is the virtual desktop and higher IDs are secondary displays; both are available only for read-only capture in this lightweight version.
- `capture`: Optional capture metadata used by `screenshot` targets and annotation.
- `default_pause_ms`: Delay after each action, from `0` to `5000`.
- `max_runtime_seconds`: Hard runtime budget, from `1` to `600`.
- `actions`: Ordered list with at most 50 entries.

## Shared Action Fields

```json
{
  "id": "unique-step-id",
  "type": "click",
  "description": "Click the Settings button",
  "confidence": 0.96,
  "sensitive": false,
  "pause_after_ms": 400
}
```

- `id`: Unique within the plan.
- `type`: One supported action type.
- `description`: State what visible control or effect is intended.
- `confidence`: Model confidence in `[0, 1]`; re-inspect low-confidence targets.
- `sensitive`: Mark actions with external, destructive, permission, or account impact. Regardless of this field, every pointer, keyboard, or scroll action requires the execution-time `--approve-sensitive` gate.
- `pause_after_ms`: Optional per-action delay override.

## Target Objects

Normalized monitor coordinates, preferred:

```json
{"space": "normalized", "monitor": 1, "x": 0.742, "y": 0.181}
```

Screenshot pixels tied to exact capture metadata:

```json
{"space": "screenshot", "x": 1420, "y": 210}
```

Absolute desktop coordinates, discouraged:

```json
{"space": "absolute", "x": 1420, "y": 210}
```

Normalized values must remain in `[0, 1]`. Screenshot coordinates are mapped through the capture's `desktop_bounds` and `image` dimensions. Absolute targets require the separate CLI flag `--allow-absolute`, must land on monitor `1`, and cannot be authorized by the plan itself.

## Action Types

### Mouse movement and clicks

```json
{"id": "move-1", "type": "move", "target": {"space": "normalized", "monitor": 1, "x": 0.5, "y": 0.5}, "duration_ms": 250}
```

```json
{"id": "click-1", "type": "click", "target": {"space": "normalized", "monitor": 1, "x": 0.5, "y": 0.5}, "button": "left", "clicks": 1}
```

Use `double_click` or `right_click` for explicit alternatives. `button` accepts `left`, `middle`, or `right`. `clicks` is limited to `1..3`, `duration_ms` to `0..5000`, and key/click intervals to `0..1000` milliseconds.

### Scrolling

```json
{"id": "scroll-1", "type": "scroll", "target": {"space": "normalized", "monitor": 1, "x": 0.8, "y": 0.75}, "amount": -6}
```

Positive values scroll up; negative values scroll down. Move to `target` before scrolling when one is provided.

### Typing and keys

```json
{"id": "type-1", "type": "type", "text": "example", "interval_ms": 25, "method": "keys"}
```

Use `method: "keys"` for ASCII keystrokes. Use `method: "clipboard"` for Unicode text; the runner temporarily replaces the clipboard, pastes, and restores the prior clipboard value on a best-effort basis. Do not store secrets in `text`.

```json
{"id": "press-1", "type": "press", "key": "enter", "presses": 1}
```

```json
{"id": "hotkey-1", "type": "hotkey", "keys": ["ctrl", "l"]}
```

Use `"command"` instead of `"ctrl"` for macOS application shortcuts. Do not store secrets in `text`.

### Waiting and evidence

```json
{"id": "wait-1", "type": "wait", "seconds": 1.5}
```

```json
{"id": "shot-1", "type": "screenshot", "label": "after-save"}
```

The runner always captures a final screenshot after a successful execution.

## Execution Gates

- Run `validate` before loading GUI backends.
- Run `annotate` against the exact source capture; it requires GUI dependencies and an accessible interactive desktop session, rejects stale captures or changed layouts, and labels markers with current resolved input coordinates.
- Run `run` without `--execute` to resolve all targets against the current display layout without sending input. This requires the GUI dependencies and an accessible interactive desktop session.
- Add `--execute --approve-sensitive` only after the user approves the reviewed plan. Pointer, keyboard, and scroll actions always trigger this gate.
- Add `--allow-absolute` only for a deliberately fixed display layout.
- Captures older than five minutes are rejected during execution. `--allow-stale-capture` exists only for deliberate diagnostics.
- `--countdown` defaults to three seconds before execution.

## Result Contract

Commands print JSON to stdout. A completed execution report contains:

- `status`: `completed`
- `actions_total` and `actions_completed`
- `artifacts_dir`
- `final_capture`: PNG and capture metadata paths
- `step_results`: action status and resolved input coordinates
- `warnings`: scale, portability, or safety warnings

A dry-run report contains `status: "dry_run"`, `approval_required_actions`, `resolved_actions`, and `execute_required: true`; it does not contain a final screenshot.

Non-zero exits return JSON on stderr with `status`, `error_type`, and `message`. Exit code `2` denotes a controlled preflight error, `3` an execution failure with a report when possible, and `130` a user interrupt.
