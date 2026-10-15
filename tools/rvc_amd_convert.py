"""In-app native RVC v2/F0 PTH→ONNX conversion without opening vc-rs GUI.

The helper is built from the upstream MIT vc-convert Rust library and bundled
on the experimental branch by GitHub Actions. Never execute PTH as pickle in
the main Python process. User-selected files are untrusted; vc-convert parses
tensors without executing Python pickle objects.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from tools.rvc_amd_bridge import ROOT, CREATION_FLAGS, VcRsError


def find_converter() -> Path:
    for candidate in (
        ROOT / "engines" / "vc-rs" / "rvcstudio-convert.exe",
        ROOT / "tools" / "bin" / "rvcstudio-convert.exe"
    ):
        if candidate.is_file():
            return candidate
    raise VcRsError(
        "PTH dönüştürücü eksik. Güncel AMD geliştirme dalının ZIP dosyasını "
        "yeniden indirip scripts/install_amd_ui_extension.ps1 çalıştır. "
        "Dönüştürücü, MIT lisanslı vc-convert Rust kütüphanesinden oluşturulur."
    )


def convert_pth(pth: str, progress=lambda _: None) -> Path:
    source = Path(pth).expanduser()
    if not source.is_file() or source.suffix.lower() != ".pth":
        raise VcRsError("Dönüştürme için mevcut bir .pth model dosyası seç.")
    target = source.with_suffix(".onnx")
    if target.exists():
        # Never destroy a pre-existing user ONNX model.
        raise VcRsError("Bu modelin ONNX dosyası zaten var: " + str(target))
    exe = find_converter()
    proc = subprocess.Popen(
        [str(exe), str(source)], cwd=str(exe.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        bufsize=1, creationflags=CREATION_FLAGS
    )
    messages = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line:
            progress(line)
            messages.append(line)
    code = proc.wait()
    if code != 0 or not target.is_file():
        raise VcRsError("ONNX dönüştürme başarısız: " + " ".join(messages[-8:])[:900])
    return target
