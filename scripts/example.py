#!/usr/bin/env python3
"""Create a minimal plan file for adapting to a fresh screenshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a starter computer-use plan")
    parser.add_argument("--output", required=True)
    parser.add_argument("--capture", required=True)
    parser.add_argument("--goal", default="Describe the intended visible outcome")
    args = parser.parse_args()

    plan = {
        "version": "1.0",
        "goal": args.goal,
        "success_criteria": ["Replace with a visible condition that proves success"],
        "monitor": 1,
        "capture": str(Path(args.capture).expanduser().resolve()),
        "default_pause_ms": 350,
        "max_runtime_seconds": 120,
        "actions": [
            {
                "id": "replace-me",
                "type": "click",
                "description": "Replace with the intended control",
                "confidence": 0.0,
                "sensitive": False,
                "target": {"space": "normalized", "monitor": 1, "x": 0.5, "y": 0.5},
            }
        ],
    }
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
