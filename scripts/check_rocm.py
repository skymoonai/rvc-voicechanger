"""Smoke-test ROCm PyTorch on Windows; does not load RVC model weights."""
import sys
import os


def main():
    try:
        import torch
        from torch.nn import functional as F
    except Exception as exc:
        print("PyTorch import failed:", exc, file=sys.stderr)
        return 2

    hip = getattr(torch.version, "hip", None)
    if not hip:
        print("Not an AMD ROCm PyTorch build; detected torch:", torch.__version__, file=sys.stderr)
        return 3
    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        print("ROCm PyTorch installed but no usable AMD GPU was detected.", file=sys.stderr)
        return 4

    try:
        device = torch.device("cuda:0")  # AMD HIP uses the torch.cuda API.
        with torch.inference_mode():
            wave = torch.linspace(-1, 1, 4096, device=device, dtype=torch.float32)
            kernel = torch.ones(1, 1, 9, device=device, dtype=torch.float32) / 9
            filtered = F.conv1d(wave.view(1, 1, -1), kernel, padding=4)
            spectrum = torch.stft(
                wave, n_fft=512, hop_length=128, return_complex=True
            )
            torch.cuda.synchronize(device)
            assert filtered.shape[-1] == 4096
            assert spectrum.shape[0] == 257
            assert torch.isfinite(filtered).all().item()
        props = torch.cuda.get_device_properties(device)
        arch = str(getattr(props, "gcnArchName", "") or "")
        required = os.environ.get("RVC_ROCM_REQUIRE_GFX", "").strip().lower()
        if required and not (arch.lower() == required or arch.lower().startswith(required + ":")):
            print(f"ROCm target mismatch: HIP reported {arch or '<unknown>'}; expected {required}. "
                  "Do not force another GFX architecture with HSA_OVERRIDE_GFX_VERSION.",
                  file=sys.stderr)
            return 6
    except Exception as exc:
        print("ROCm GPU convolution/STFT inference test failed:", repr(exc), file=sys.stderr)
        return 5

    print("ROCm PyTorch:", torch.__version__, "HIP:", hip)
    print("AMD GPU:", torch.cuda.get_device_name(0))
    print("ROCm architecture:", getattr(props, "gcnArchName", "unavailable"))
    print("Reported total HIP device memory (may be misleading for integrated GPUs):",
          round(props.total_memory / 1024**3, 2), "GiB")
    print("FP32 convolution and STFT: PASS")
    print("RVC Studio real-time voice test: NOT YET RUN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
