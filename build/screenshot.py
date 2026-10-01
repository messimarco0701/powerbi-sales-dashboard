"""
Takes screenshots of the report pages open in Power BI Desktop, for the README.

Uses PrintWindow instead of a normal screen grab, so it works even when other
windows are on top of Power BI.

Usage:
    python build/screenshot.py                     # current page -> screenshots/_current.png
    python build/screenshot.py "Sales overview" overview.png
    python build/screenshot.py --texts             # also print the texts on screen (handy for errors)
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

import psutil
from PIL import Image
from pywinauto import Application

OUT_DIR = Path(__file__).resolve().parents[1] / "screenshots"


def pbi_window():
    pids = [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("pbidesktop")]
    if not pids:
        sys.exit("Power BI Desktop is not running")
    app = Application(backend="uia").connect(process=pids[0])
    wins = [w for w in app.windows() if w.is_visible()]
    return max(wins, key=lambda w: w.rectangle().width() * w.rectangle().height()), wins


def print_window(hwnd) -> Image.Image:
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(header), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def go_to_page(win, name: str) -> None:
    for c in [d for d in win.descendants() if d.window_text().strip() == name]:
        for method in ("select", "invoke"):
            try:
                getattr(c, method)()
                time.sleep(5)
                return
            except Exception:
                pass
    raise SystemExit(f"Page tab '{name}' not found")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    win, wins = pbi_window()
    if len(args) == 2:
        go_to_page(win, args[0])
        out = OUT_DIR / args[1]
    else:
        out = OUT_DIR / "_current.png"
    OUT_DIR.mkdir(exist_ok=True)
    for w in wins:
        if w.handle != win.handle:
            dialog = out.with_name(f"{out.stem}_dialog_{w.handle}.png")
            print_window(w.handle).save(dialog)
            print("dialog saved", dialog.name)
    print_window(win.handle).save(out)
    print("saved", out.name)
    if "--texts" in sys.argv:
        for w in wins:
            for d in w.descendants(control_type="Text"):
                t = d.window_text().strip()
                if t:
                    print("TXT:", t[:300])


if __name__ == "__main__":
    main()
