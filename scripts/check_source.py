#!/usr/bin/env python3
"""Syntax and repository hygiene checks. No application code is executed."""
from pathlib import Path
import ast
import sys

root = Path(__file__).resolve().parent.parent
bad = []
for p in root.rglob("*.py"):
    if ".venv" in p.parts or "__pycache__" in p.parts:
        continue
    try:
        ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
    except (SyntaxError, UnicodeError) as exc:
        bad.append((str(p.relative_to(root)), str(exc)))
for path, reason in bad:
    print(f"FAIL: {path}: {reason}")
print(f"Python syntax: {'FAIL' if bad else 'OK'}")
for rel in ("modern_launcher.py", "realtime_gui.py", "LICENSE", "THIRD_PARTY_NOTICES.txt"):
    if not (root / rel).is_file():
        print(f"MISSING: {rel}")
        bad.append((rel, "missing"))
sys.exit(1 if bad else 0)
