"""Verified, resumable-in-the-next-run AMD support model preparation.

Assets belong to the user's own vc-rs data folder and are never bundled with
the MIT-licensed app. Upstream ContentVec/RMVPE are GPL-3.0 (wok000/weights_gpl).
Download is opted into by choosing the AMD tab; done off the Qt main thread.
"""
from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from pathlib import Path

from tools.rvc_user_state import data_root

BASE = data_root() / "engines" / "vc-rs" / "models"
HF_REVISION = "d1b578cfcdf944dd3910786f7604754c79234b36"
MODELS = {
    "content_vec_500.onnx": {
        "url": "https://huggingface.co/wok000/weights_gpl/resolve/" + HF_REVISION + "/content-vec/contentvec-f.onnx",
        "size": 378550151,
        "sha256": "4b31ed3d95a568fab7952de923ff7f7d3d17128ea6fce69f665509d24c3156db",
    },
    "rmvpe.onnx": {
        "url": "https://huggingface.co/wok000/weights_gpl/resolve/" + HF_REVISION + "/rmvpe/rmvpe_20231006.onnx",
        "size": 362003174,
        "sha256": "84f0586308e36157f75b77c8591bf636d6719c0c4ba95f8faf3df479e7566219",
    },
}


def verified(path: Path, meta: dict) -> bool:
    """Check pinned source hash, not only file existence or approximate size."""
    try:
        if path.stat().st_size != meta["size"]:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for part in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(part)
        return digest.hexdigest() == meta["sha256"]
    except OSError:
        return False


def paths() -> tuple[str, str]:
    """Paths to existing *verified* support files; empty when missing."""
    return tuple(str(BASE / name) if verified(BASE / name, meta) else ""
                 for name, meta in MODELS.items())


def ensure_models(progress, cancel: threading.Event | None = None) -> tuple[Path, Path]:
    """Stream data to .download files, SHA-verify then atomically promote.

    `progress(message, fraction)` runs in a background thread and must not touch
    Qt widgets directly; signal through a queue or QObject instead.
    """
    BASE.mkdir(parents=True, exist_ok=True)
    for name, meta in MODELS.items():
        target = BASE / name
        if verified(target, meta):
            progress(name + " hazır", 1.0)
            continue
        temp = BASE / (name + ".download")
        progress(name + " indiriliyor (GPL-3.0)", 0.0)
        try:
            req = urllib.request.Request(meta["url"], headers={"User-Agent": "RVCStudio-AMD/1.5.2"})
            with urllib.request.urlopen(req, timeout=35) as response, temp.open("wb") as out:
                digest = hashlib.sha256()
                total = 0
                while True:
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError("Model indirme iptal edildi.")
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > meta["size"]:
                        raise ValueError("Destek modeli beklenenden büyük; indirme durduruldu.")
                    out.write(block)
                    digest.update(block)
                    progress(name + f" — {total//1048576} MB / {meta['size']//1048576} MB",
                             min(0.99, total / meta["size"]))
            if total != meta["size"] or digest.hexdigest() != meta["sha256"]:
                raise ValueError(name + ": boyut veya SHA-256 doğrulaması başarısız.")
            os.replace(temp, target)
            progress(name + " indirildi ve doğrulandı", 1.0)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    return BASE / "content_vec_500.onnx", BASE / "rmvpe.onnx"


def start_async(progress, finished, failed, cancel=None):
    def work():
        try:
            result = ensure_models(progress, cancel)
        except Exception as exc:
            failed(str(exc))
        else:
            finished(result)
    worker = threading.Thread(target=work, name="RVCAMD-model-download", daemon=True)
    worker.start()
    return worker
