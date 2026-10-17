"""Strict experimental HIP selection for RVC Studio Radeon 680M.

"Force ROCm" means *refuse CPU/DirectML fallback*, not spoofing a GPU or
overriding gfx architecture. Validates real PyTorch HIP kernels before the
model is loaded. AppSandbox GPU-PV cannot be made HIP-capable by this code.
"""
from __future__ import annotations

import os

FORCE_MODE = "AMD ROCm'yi Zorla (Deneysel)"


class RocmUnavailable(RuntimeError):
    pass


def wants_force(mode: str | None = None) -> bool:
    return mode == FORCE_MODE or os.environ.get("RVC_ROCM_FORCE", "0") == "1"


def verify_gpu(torch_module=None, required_gfx=None, device_index=0):
    """Return (HIP torch.device, device name, actual architecture), else fail.

    Gpu-PV can expose an AMD adapter without ROCm/HSA device queues, so
    enumeration without a synchronized compute test would give false positives.
    """
    if torch_module is None:
        import torch as torch_module
    torch = torch_module
    hip = getattr(getattr(torch, "version", None), "hip", None)
    if not hip:
        raise RocmUnavailable(
            "Bu Python ortamı ROCm/HIP PyTorch içermiyor. "
            "TheRock ROCm sanal ortamını kur; CPU/CUDA sürümü ROCm'ye dönüştürülemez."
        )

    try:
        if not torch.cuda.is_available() or torch.cuda.device_count() <= device_index:
            raise RocmUnavailable(
                "ROCm PyTorch yüklü fakat kullanılabilir HIP GPU bulunamadı. "
                "AppSandbox GPU-PV'nin Radeon adı göstermesi HIP erişimi sağlamaz."
            )
        device = torch.device("cuda:" + str(device_index))  # ROCm uses torch.cuda API
        properties = torch.cuda.get_device_properties(device)
        architecture = str(getattr(properties, "gcnArchName", "") or "")
        expected = (required_gfx if required_gfx is not None else
                    os.environ.get("RVC_ROCM_REQUIRE_GFX", "gfx1035")).strip().lower()
        if expected and not (architecture.lower() == expected or
                             architecture.lower().startswith(expected + ":")):
            raise RocmUnavailable(
                f"HIP GPU mimarisi uyuşmuyor: {architecture or 'bilinmiyor'} "
                f"(gereken {expected}). Başka mimariyi taklit etmek güvenli değil."
            )
        with torch.inference_mode():
            wave = torch.linspace(-1, 1, 4096, device=device, dtype=torch.float32)
            weights = torch.ones(1, 1, 9, device=device, dtype=torch.float32) / 9
            smoothed = torch.nn.functional.conv1d(
                wave.reshape(1, 1, -1), weights, padding=4
            )
            fft = torch.stft(wave, n_fft=512, hop_length=128, return_complex=True)
            torch.cuda.synchronize(device)
            if smoothed.shape[-1] != 4096 or fft.shape[0] != 257:
                raise RocmUnavailable("HIP GPU matematik testi beklenmeyen çıktı üretti.")
            if not torch.isfinite(smoothed).all().item():
                raise RocmUnavailable("HIP GPU hesaplaması sonlu olmayan değerler üretti.")
        name = str(torch.cuda.get_device_name(device_index))
        return device, name, architecture
    except RocmUnavailable:
        raise
    except Exception as exc:
        raise RocmUnavailable(
            "ROCm'yi zorla seçtik fakat GPU HIP konvolüsyon/STFT işlemlerini "
            f"çalıştıramadı: {type(exc).__name__}: {exc}. "
            "AppSandbox sürücüsü veya gfx1035 TheRock paketi desteklemiyor olabilir."
        ) from exc
