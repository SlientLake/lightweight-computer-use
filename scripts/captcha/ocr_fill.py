#!/usr/bin/env python3
"""Read a character/number captcha from a screen region and fill an input box.

Two-stage human-in-the-loop design:
  1. `read`  - capture a region, run OCR, print the recognized text (do not type it)
  2. `fill`  - after the human confirms the text, paste it into the focused input

For captchas the human can read but OCR fails on, `read --show` crops and saves
the region so the human types the text; never let OCR guesses go unreviewed.

Usage:
  python ocr_fill.py read   --x 700 --y 300 --w 200 --h 60 [--show]
  python ocr_fill.py fill   --text "A7K2" [--click-x N --click-y N]

OCR backend: RapidOCR (local, onnxruntime) if installed; otherwise instruct
the caller to install it or use an approved cloud OCR endpoint.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pyautogui

pyautogui.FAILSAFE = True


def grab_region(x: int, y: int, w: int, h: int, scale: int = 3) -> "object":
    import mss
    from PIL import Image

    with mss.MSS() as sct:
        img = sct.grab({"left": x, "top": y, "width": w, "height": h})
    im = Image.frombytes("RGB", img.size, img.rgb)
    # upscale so small distorted glyphs become OCR-readable
    return im.resize((w * scale, h * scale), Image.LANCZOS)


def ocr_image(im) -> str:
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        result, _ = engine(im)
        if not result:
            return ""
        return "".join(item[1] for item in result).strip()
    except ImportError:
        print(
            "OCR backend missing. Install in the skill venv:\n"
            "  pip install rapidocr-onnxruntime\n"
            "Or wire an approved cloud OCR endpoint here.",
            file=sys.stderr,
        )
        raise SystemExit(3)


def cmd_read(args: argparse.Namespace) -> int:
    im = grab_region(args.x, args.y, args.w, args.h)
    if args.show:
        out = Path(rf"C:/Users/99780/.workbuddy/skills/lightweight-computer-use/.last_captcha.png")
        im.save(out)
        print(f"saved {out}")
    text = ocr_image(im)
    print(f"OCR_TEXT: {text}")
    print("Review the image/text before filling - do not auto-submit OCR guesses.")
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    if args.click_x is not None and args.click_y is not None:
        pyautogui.click(args.click_x, args.click_y)
        time.sleep(0.4)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.15)
    # clipboard paste avoids IME hijacking of typed characters
    import pyperclip

    pyperclip.copy(args.text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    print("FILLED (pasted via clipboard; verify on screen before submitting)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Captcha region OCR read/fill")
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read")
    p_read.add_argument("--x", type=int, required=True)
    p_read.add_argument("--y", type=int, required=True)
    p_read.add_argument("--w", type=int, required=True)
    p_read.add_argument("--h", type=int, required=True)
    p_read.add_argument("--show", action="store_true")
    p_read.set_defaults(func=cmd_read)

    p_fill = sub.add_parser("fill")
    p_fill.add_argument("--text", required=True)
    p_fill.add_argument("--click-x", type=int)
    p_fill.add_argument("--click-y", type=int)
    p_fill.set_defaults(func=cmd_fill)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
