#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"

def convert(path: Path):
    b = path.read_bytes()
    try:
        # try decoding as utf-8 first
        b.decode("utf-8")
        return False
    except UnicodeDecodeError:
        # decode as cp1252 (windows-1252) and re-save as utf-8
        text = b.decode("cp1252")
        path.write_text(text, encoding="utf-8")
        return True

def main():
    if not TEMPLATES_DIR.exists():
        print("templates/ folder not found:", TEMPLATES_DIR)
        return 1
    changed = []
    for p in TEMPLATES_DIR.rglob("*.html"):
        try:
            if convert(p):
                changed.append(p)
        except Exception as ex:
            print("Error converting", p, ex)
    if changed:
        print("Re-encoded files to UTF-8:")
        for p in changed:
            print(" -", p)
    else:
        print("No re-encoding needed; all files are valid UTF-8.")
    return 0

if __name__ == "__main__":
    sys.exit(main())