#!/usr/bin/env python3
"""Detect the puzzle-gap position in a slider-captcha background image.

Pipeline: capture a screen region -> grayscale -> blur -> Canny edges ->
contour filtering (area + aspect ratio) -> report the gap center in full-screen
coordinates, so the drag distance = gap_x - handle_x.

Usage:
  python gap_detect.py --bg-x 400 --bg-y 300 --bg-w 300 --bg-h 170 --handle-x 840

Output: JSON to stdout, e.g. {"gap_x": 1123, "gap_y": 470, "distance": 283, "confidence": 0.82}
Exit codes: 0 found, 4 not found, 3 missing backend, 2 bad input.
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect slider-captcha gap position")
    parser.add_argument("--bg-x", type=int, required=True, help="background image left (full-screen coords)")
    parser.add_argument("--bg-y", type=int, required=True, help="background image top")
    parser.add_argument("--bg-w", type=int, required=True, help="background image width")
    parser.add_argument("--bg-h", type=int, required=True, help="background image height")
    parser.add_argument("--handle-x", type=int, required=True, help="slider handle center x (full-screen coords)")
    parser.add_argument("--handle-y", type=int, required=True, help="slider handle center y")
    parser.add_argument("--min-area", type=int, default=800, help="min contour area (scaled px)")
    parser.add_argument("--save-debug", default="", help="optional path to save the edge image")
    args = parser.parse_args()

    try:
        import cv2
        import numpy as np
    except ImportError:
        print("opencv-python-headless required: pip install opencv-python-headless", file=sys.stderr)
        return 3

    import mss
    from PIL import Image

    with mss.MSS() as sct:
        img = sct.grab({"left": args.bg_x, "top": args.bg_y, "width": args.bg_w, "height": args.bg_h})
    pil = Image.frombytes("RGB", img.size, img.rgb)
    scale = 3  # upscale small captchas so edges survive Canny
    pil = pil.resize((args.bg_w * scale, args.bg_h * scale), Image.LANCZOS)

    gray = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 60, 180)
    if args.save_debug:
        Image.fromarray(edges).save(args.save_debug)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        if area < args.min_area * scale:
            continue
        aspect = w / max(h, 1)
        if not 0.6 <= aspect <= 1.8:  # puzzle gaps are near-square
            continue
        if w > args.bg_w * scale * 0.6:  # not the slider rail itself
            continue
        candidates.append((area, x, y, w, h))

    if not candidates:
        print(json.dumps({"found": False, "reason": "no gap-like contour"}))
        return 4

    # the gap is typically the strongest near-square contour on the right half
    candidates.sort(key=lambda c: c[0], reverse=True)
    best = candidates[0]
    _, x, y, w, h = best
    gap_center_scaled_x = x + w / 2
    gap_center_scaled_y = y + h / 2
    gap_screen_x = args.bg_x + gap_center_scaled_x / scale
    gap_screen_y = args.bg_y + gap_center_scaled_y / scale

    distance = int(round(gap_screen_x - args.handle_x))
    confidence = min(1.0, best[0] / (args.min_area * scale * 4))
    print(
        json.dumps(
            {
                "found": True,
                "gap_x": int(round(gap_screen_x)),
                "gap_y": int(round(gap_screen_y)),
                "distance": distance,
                "confidence": round(confidence, 2),
                "candidates": len(candidates),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
