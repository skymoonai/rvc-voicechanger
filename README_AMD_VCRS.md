# RVC Studio — AMD vc-rs preview (experimental)

This branch adds an **opt-in AMD Motoru** tab powered by the MIT-licensed
[shirohata/vc-rs](https://github.com/shirohata/vc-rs) Windows ML executable.
The existing Python/PyTorch RVC Studio audio callback, NVIDIA CUDA support,
models, profiles, normal DirectML path, Soundpad and file conversion remain unchanged.

**Status:** integration scaffold / early beta. Python syntax + subprocess
unit tests are checked by GitHub Actions. **No AMD GPU or audio hardware validation
has been performed.** Working audio, latency, and acceleration are not guaranteed.

## Windows installation

1. Install RVC Studio normally (for example at `C:\RVCStudio`).
2. Open PowerShell (administrator privileges are not required) and run:

   ```powershell
   & 'C:\RVCStudio\scripts\install_vcrs_amd.ps1'
   ```

   The script downloads **vc-rs v0.5.3 Windows ML GUI+CLI** from its official
   release, checks pinned SHA-256
   `3f263b53715572aa0c5f6b6b30f0acea672de3c3d04f200125bd8c34cb67e758`,
   and installs to `C:\RVCStudio\engines\vc-rs`. Run only if you trust the
   upstream release; there is no automatic binary download on app launch.
   For a custom location set `RVC_STUDIO_VCRS_EXE` to the absolute `vc-rs.exe` path.

3. Install **Windows App SDK Runtime 2.1 or later** if not installed:
   [Microsoft's official download page](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads).
   This dependency is not bundled or installed by our script.
4. In RVC Studio select **AMD Motoru → PTH → ONNX dönüştürücüyü aç**. The
   vc-rs GUI can convert supported **RVC v2 / F0** `.pth` voice models to
   `.onnx` and offers **Download required models** for ContentVec and RMVPE.
   Read the third-party model license terms before downloading.
5. Select the resulting voice `.onnx` in the AMD tab (or provide a PTH
   whose same-name ONNX is next to it). Select ContentVec and RMVPE `.onnx`
   paths if they aren't found automatically.
6. Prefer **DirectML** first. Choose a WASAPI input/output or leave blank
   for vc-rs defaults. **MIGraphX is unverified** and needs a compatible AMD GPU
   and provider installed through Windows ML.
7. Stop regular RVC Studio voice conversion, then click
   **AMD Motorunu Başlat**. Watch the embedded log for model-loading and
   initialization errors. Restart the AMD engine after modifying settings.

## Architecture

```text
RVC Studio PySide6 UI
       |
       | subprocess.Popen + log reader thread
       v
engines/vc-rs/vc-rs.exe run --provider windowsml-directml
       |
       +-- vc-rs Rust WASAPI callbacks -> ring buffers -> worker
       +-- ONNX Runtime/Windows ML: ContentVec + RMVPE + RVC generator
       +-- vc-rs native chunk joining/resampling -> WASAPI output
```

The executable is isolated in a separate process, not imported into Python.
This preserves existing PyTorch dependency versions and prevents two inference
frameworks from colliding within a process. The AMD tab does *not* route audio
through RVC Studio's Python SoundDevice callback.

### Supported in this preview

- Windows x64, compatible Windows ML GPU/CPU environment.
- Explicit `windowsml-directml` and experimental `windowsml-migraphx`.
- RVC v2 F0 model in ONNX format; conversion through vc-rs GUI.
- Default or explicitly named WASAPI input/output.
- Backend selection, block/chunk size, initial pitch, start, stop, logs.
- Independent settings saved under the user data folder.

### Not yet integrated

- INDEX retrieval, real-time formant or F0-algorithm switching, RVC Studio's
  AP-BWE/noise/monitor/live microphone stats, Soundpad routing, hotkeys,
  A/B tests, and RVC Studio model library profile sync on change.
- Automatic PTH conversion in the Python panel (the vc-rs GUI handles it).
- Hot changes to pitch or microphone selection; stop and start to apply.
- AMD GPU detection and verified per-operator GPU execution/benchmarks.
- An AMD-specific installer that bundles the main RVC Studio app.

Do not claim support for these features until implemented. The **AMD tab
and original engine must not run simultaneously** to avoid fighting for devices.
Closing RVC Studio terminates its own vc-rs subprocess.

## Quality and performance validation

On AMD hardware, compare the Python DirectML engine with the vc-rs Windows ML
engine using the same voice, source, I/O devices, output rate and chunk settings.
Record actual end-to-end latency, sustained inference time (p50/p95/p99),
audio underruns, audible artifacts and CPU/GPU usage. The vc-rs upstream repo
has **not** validated MIGraphX on physical AMD hardware. A listed EP does
not prove that all ONNX operators run on the GPU.

## Licensing

vc-rs source: MIT, Copyright (c) 2026 shirohata, retain original copyright
and license notice if any vc-rs source is copied into RVC Studio. This preview
launches an independently downloaded vc-rs binary; it does **not** copy or
redistribute its source or third-party model weights. Read the vc-rs release
and third-party notices; model weights have their own license (some GPL-3.0).
If creating a unified installer later, perform a separate license review.

## Development checks

```powershell
py -3.12 -m py_compile tools/rvc_amd_bridge.py tools/rvc_modern_qt.py
py -3.12 -m unittest discover -s tests -p test_amd_bridge.py -v
```

Upstream version pin: [vc-rs v0.5.3](https://github.com/shirohata/vc-rs/releases/tag/v0.5.3).
