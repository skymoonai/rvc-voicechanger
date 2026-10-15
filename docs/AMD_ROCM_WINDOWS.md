# Experimental AMD ROCm on Windows / Windows AMD ROCm (Deneysel)

> **Status: source-only beta preparation.** This branch does **not** contain a new prebuilt
> Windows installer or evidence of successful RVC real-time inference on AMD hardware.
> Do not announce it as a tested production AMD release.

## Hangi ekran kartları?

This experimental profile pins **ROCm PyTorch 7.2.1 on Windows 11**, so it follows
AMD's **version-specific Windows/PyTorch** support matrix:

- Radeon **RX 9070, RX 9070 XT, RX 9060 XT**
- Radeon **RX 7900 XTX, RX 7700**
- Radeon **AI PRO R9700, PRO W7900, PRO W7900 Dual Slot**
- Select **Ryzen AI 300/400** and **Ryzen AI Max** APUs in the AMD Ryzen support matrix

This is **not** an exhaustive list for every ROCm version or Linux.
For example RX 6600, RX 6700 XT, RX 7600, RX 7800 XT, RX 7900 XT are not
officially listed for **Windows ROCm PyTorch 7.2.1**; use the regular DirectML
profile if you have one of those cards. For ROCm versions newer than 7.2.1,
consult the current AMD documentation separately; do not extrapolate support.

Official matrices:
- [Radeon Windows 7.2.1 support](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html)
- [Ryzen Windows 7.2.1 support](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityryz/windows/windows_compatibility.html)

## Ön koşullar

1. **Windows 11 x64**, Python **3.12 x64** with the `py` launcher.
2. An AMD-supported GPU and **Adrenalin 26.2.2 driver** for ROCm 7.2.1 as prescribed by AMD.
3. A clean checkout of this source branch and a good internet connection.
4. Model assets/weights and audio devices needed for the normal RVC Studio source
   checkout, which are **not distributed** through this source repository.
5. Enough disk space for large AMD ROCm/PyTorch wheel downloads and the separate venv.

[Official AMD wheel install guide](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html)

## Kaynak koddan deneysel kurulum

Open PowerShell in the source checkout:

```powershell
git checkout feature/amd-rocm-windows-experimental
powershell -ExecutionPolicy Bypass -File .\scripts\setup_rocm_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run_rocm_windows.ps1
```

The setup script creates **`.venv-rocm`** and installs official AMD ROCm 7.2.1
packages and PyTorch 2.9.1 AMD wheels into **that environment only**.
It does **not** replace the installed RVC Studio release, modify the DirectML/CUDA
Python environments or install/uninstall video drivers. It refuses unknown
hardware unless explicitly invoked with `-AllowUnsupportedGpu` (not recommended).

The runner first checks that `torch.version.hip` exists and verifies basic
GPU convolution and STFT execution. **Passing this probe is not the same as
successful RVC conversion.** Actual HuBERT/RMVPE/RVC model operations, audio
stream timing, precision and hardware-dependent operators still need tests.

### Sorun varsa / Troubleshooting

- **AMD device not detected:** verify Windows driver, GPU model, Windows 11 and
  `py -3.12`. See the official AMD support matrix and prerequisites.
- **Wheel installation failed:** keep the full console error; ROCm's Windows
  wheels may require specific driver versions and depend on upstream servers.
- **PyTorch GPU probe failed:** do not force ROCm; use the regular DirectML build.
- **RVC HuBERT/RMVPE model failed:** ROCm PyTorch 2.9 uses a different library
  stack from the pinned DirectML PyTorch 2.4.1 setup. Collect logs and reproduce
  with the same model and settings; this is still an unvalidated combination.
- **Realtime stutter:** test with the same model and voice settings on DirectML
  and ROCm. Compare actual audio dropout count, latency, CPU/GPU utilization and
  F0 method. A more powerful backend does not fix microphone/driver buffer issues.
- **Rollback:** simply return to the existing RVC Studio installation/DirectML
  launcher. Optional: delete `.venv-rocm` when not using this experiment.

When reporting a bug, include AMD GPU model, Windows version, driver version,
ROCm/PyTorch versions, available VRAM, audio device, F0 method, block/buffer
size, logs, and whether basic GPU verification passed. Do not upload personal
voice samples or licensed models without permission.

## Why is the backend called `cuda` on AMD?

PyTorch's ROCm/HIP build exposes GPU devices via the standard `torch.cuda`
interface. RVC Studio detects the PyTorch build through `torch.version.hip`
so the UI says **AMD ROCm**, not NVIDIA CUDA. ROCm defaults to **FP32** and
**CUDA Graph disabled** until real-world RVC accuracy/latency tests validate
faster paths.
