# RVC Studio — ROCm Radeon 680M / gfx1035 experimental branch

**Branch:** `feature/rocm-gfx1035-studio-preview`

**Status:** source-only experimental integration and tested setup *logic*;
**NO GPU hardware test, NO standalone installer, NO proof of live voice conversion**.

This is the original **RVC Studio PyTorch real-time voice engine** on an
AMD/HIP backend, **not** the Rust/ONNX `vc-rs` motor. It preserves PTH voice
models and their optional FAISS INDEX, original studio interface, microphone
routing, Soundpad/profile/file-processing UI where applicable. It does not
change NVIDIA main, AMD Edition or the previous ROCm 7.2.1 branch.

## GPU support — why this branch exists

The host laptop's Ryzen 7 7735HS uses Radeon **680M / gfx1035** (RDNA 2
integrated GPU).

* AMD's standard Windows/PyTorch ROCm 7.2.1 tables **do not list Radeon
  680M** as supported. Installing the official 7.2.1 wheel with a
  `-AllowUnsupportedGpu` switch does **not** create real support.
* AMD's separate experimental
  [TheRock](https://github.com/ROCm/TheRock/blob/main/RELEASES.md) system
  **does** publish Windows ROCm/PyTorch multi-arch nightly packages for
  `device-gfx1035`, with the architecture showing sanity tested / release
  ready in [TheRock supported targets](https://github.com/ROCm/TheRock/blob/main/SUPPORTED_GPUS.md).
  Those two statuses are not an AMD commitment that every Windows laptop
  and every nightly PyTorch release works with RVC.
* [TheRock Windows status](https://github.com/ROCm/TheRock/blob/main/docs/development/windows_support.md)
  explicitly calls this work immature. Install and driver support can change
  from night to night.

**AppSandbox warning:** The guest previously reported `AMD Radeon(TM)
Graphics` with Microsoft display driver `10.0.26100.1150`, plus
`Microsoft Hyper-V Video` and `App Sandbox Virtual Display Adapter`.
This may be GPU paravirtualization sufficient for DirectML but not native HIP
queues. A Windows display adapter listing is **not** a ROCm capability test.
The setup blocks non-AMD-signed GPU drivers by default. You can pass
`-AllowVirtualizedGpu` to *attempt* experimental installation, but
`check_rocm.py` will still refuse to start the app unless real FP32 HIP
convolution, STFT, GPU synchronize and `gfx1035` checks pass.

## Architecture

```
RVC Studio existing Qt "Ses Dönüşümü" UI
           |
original PyTorch RVC (PTH + INDEX)
         /    \
ContentVec     RMVPE
         \    /
    PyTorch AMD HIP via torch.cuda API
           |
TheRock experimental ROCm Windows PyTorch / device-gfx1035
           |
actual Radeon 680M HIP runtime (NOT DirectML, NOT vc-rs)
```

* RTX 4060 CUDA/normal app in `main` is not modified.
* Separate **`.venv-rocm-gfx1035`** Python 3.12 environment.
* No video-driver install/uninstall, no ROCm DLL overwrite, no old vc-rs
  process and no model conversion to ONNX.
* ROCm starts in **FP32**, disables CUDA Graph until GPU/model proof.
* Integrated GPUs can report little dedicated memory; a previously
  hardcoded NVIDIA **4 GiB VRAM cutoff** is removed for ROCm **only**.
* Config probes actual synchronized HIP GPU convolution, not just a GPU
  adapter display name. If HIP fails, launcher does not start and does not
  relabel CPU inference as ROCm.

## Setup — source checkout only, NOT existing installed RVC Studio setup.exe

Choose a source checkout separate from `C:\RVCStudio`; the full released
v1.5.2 executable/GUI files are not identical to public source files.
Back up existing models and user profiles before experimenting.

1. Install **Python 3.12 x64** with Windows `py` launcher; update the AMD
   Windows driver on the **host** according to TheRock guidance, not by copying
   host driver DLLs into AppSandbox. Real ROCm-compatible driver is essential.
2. Download the
   [separate branch ZIP](https://github.com/skymoonai/rvc-voicechanger/archive/refs/heads/feature/rocm-gfx1035-studio-preview.zip),
   extract to a separate folder, e.g.
   `C:\RVCStudio-ROCm680M`. Bring your authorized RVC weights/models into
   the source tree per `README_SOURCE.md`.
3. On a native Windows host with Radeon 680M:

```powershell
cd C:\RVCStudio-ROCm680M
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_rocm_680m_preview.ps1 -AcceptNightlyRisk
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_rocm_680m_preview.ps1
```

If AppSandbox shows the generic `AMD Radeon(TM) Graphics` adapter or a
Microsoft provider driver, setup **will refuse by default**. To intentionally
try installing TheRock wheels **despite** the VM limitation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_rocm_680m_preview.ps1 -AcceptNightlyRisk -AllowVirtualizedGpu -AllowUnrecognizedAdapter
```

This can download large unstable packages only to fail the mandatory HIP test.
**Try the host Windows installation first if possible.** It gives a much
clearer test of whether Radeon 680M actually supports this TheRock release.
Do not fake GPU architecture with `HSA_OVERRIDE_GFX_VERSION`, replace random
HIP DLLs or change the OS driver in the VM merely to pass the launch check.

### What a successful test says

`check_rocm.py` should print something like:

```text
ROCm PyTorch: ... HIP: ...
AMD GPU: AMD Radeon 680M
ROCm architecture: gfx1035...
FP32 convolution and STFT: PASS
RVC Studio real-time voice test: NOT YET RUN
```

The last line matters: a GPU tensor smoke test **does not** verify model
precision, HuBERT, RMVPE, audio callback, actual latency or distortion.

Once launched, start with pitch 0, RNNoise off, normal default RVC settings,
an authorized PTH model and the correct INDEX if available. Compare the same
model/settings with the CUDA/PyTorch or DirectML version. ROCm may or may not
improve voice quality: converting the sound via the original RVC model avoids
vc-rs's ONNX/converter differences, **but poor input audio, source voice
mismatch, misconfigured audio devices or a too-slow GPU will still sound bad**.

## Rolling back

Close the ROCm GUI and run the ordinary installed RVC Studio/NVIDIA/DirectML
launcher. The ROCm preview did not overwrite it. Optionally remove
`C:\RVCStudio-ROCm680M\.venv-rocm-gfx1035` to reclaim disk space. This
does not touch the user's normal model library, NVIDIA Python environment
or AMD vc-rs build.

## Tested vs untested

**Windows GitHub Actions**: tests check the Python source, GPU simulated
512MiB UMA GPU admission, fake GPU-PV HIP failure rejection, enforced HIP
`gfx1035` architecture check, PowerShell 5.1 script syntax and mandatory
nightly opt-in. CI runner does not contain AMD GPU nor TheRock driver.
**Not verified**: package installation against live upstream nightlies;
Windows 680M enumeration; AppSandbox pass-through; RVC realtime synthesis;
sample quality; output latency; microphone support and Soundpad.

Source references: [AMD TheRock RELEASES](https://github.com/ROCm/TheRock/blob/main/RELEASES.md),
[TheRock Windows guidance](https://github.com/ROCm/TheRock/blob/main/docs/development/windows_support.md),
[AMD ROCm standard Windows matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityryz/windows/windows_compatibility.html).
