"""vc-rs WASAPI device discovery in the background; exact names for CLI.

The CLI prints, e.g.:
  WASAPI input devices:
    Microphone (USB Audio)
  WASAPI output devices:
    Headphones (USB Audio)
"""
from __future__ import annotations

import subprocess
import re
from tools.rvc_amd_bridge import find_executable, CREATION_FLAGS


def parse_devices(output: str) -> tuple[list[str], list[str]]:
    inputs: list[str] = []
    outputs: list[str] = []
    current = None
    for line in output.splitlines():
        label = line.strip().lower()
        if label.endswith("input devices:"):
            current = inputs
            continue
        if label.endswith("output devices:"):
            current = outputs
            continue
        if not line.strip() or (not line[:1].isspace()):
            current = None
            continue
        name = line.strip()
        if current is not None and name and not name.lower().startswith("devices unavailable"):
            if name not in current:
                current.append(name)
    return inputs, outputs


def list_wasapi(timeout: int = 18) -> tuple[list[str], list[str]]:
    executable = find_executable()
    result = subprocess.run(
        [str(executable), "devices", "--audio-backend", "wasapi"],
        cwd=str(executable.parent), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=CREATION_FLAGS, check=False
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or
                            "WASAPI cihaz listesi alınamadı.")[:1000])
    return parse_devices(result.stdout)
