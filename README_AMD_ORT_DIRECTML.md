# RVC Studio AMD — ONNX Runtime DirectML Edition

**Branch:** `feature/amd-onnxruntime-directml`
**Target:** RVC Studio **1.5.2 Beta 1**, already installed in `C:\RVCStudio`
**Status:** Integrated upgrade candidate. Windows automated tests pass;
user's actual AppSandbox GPU/audio hardware and voice quality are **not**
yet validated. This is not a production-certified installer.

## What was replaced

The Rust `vc-rs.exe` **audio inference engine** is completely removed from
the realtime-microphone, offline-WAV and A/B-test code paths. Voice inference
runs as Python in its own `.venv-ort-dml` using Microsoft's
`onnxruntime-directml==1.24.2` and `DmlExecutionProvider`. The new worker
uses verified ContentVec/RMVPE ONNX support models and an RVC v2/F0 ONNX
voice model.

The unchanged legacy `rvcstudio-convert.exe` helper may remain on disk
**only as an optional PTH→ONNX export utility**. It is not an audio engine.
The original installed Qt UI, system tray, model library, profile screen,
Soundpad, updater and license workflow are deliberately preserved.

AMD ONNX Runtime supports optional external FAISS INDEX and an experimental
Protect (unvoiced consonant preservation) slider. Those run in Python/CPU
around the GPU model rather than inside the ONNX graph.

## Easy install on top of existing Rust AMD Edition

1. Close RVC Studio entirely. Back up your models/profiles.
2. Download the [full branch ZIP](https://github.com/skymoonai/rvc-voicechanger/archive/refs/heads/feature/amd-onnxruntime-directml.zip)
   and extract to a folder.
3. In that folder, run PowerShell:

       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_amd_ort_directml.ps1 -Target C:\RVCStudio

   Or download and run the single bootstrap script
   `scripts/install_amd_ort_oneclick.ps1` from this project.
4. Installer creates `C:\RVCStudio\.venv-ort-dml` and installs isolated
   Python dependencies. It **does not change** NVIDIA PyTorch or existing
   Rust vc-rs binaries. It tests for `DmlExecutionProvider` before replacing
   the AMD edition's startup hook.
5. Reopen RVC Studio. Title should say **AMD Edition · ONNX Runtime DirectML**.
   Select a voice ONNX/PTH, physical microphone input, and suitable virtual
   microphone cable output.

## Roll back to the previous Rust AMD Edition

Close RVC Studio and use the same original extracted folder:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_amd_ort_directml.ps1 -Target C:\RVCStudio -Remove

The updater creates `%LOCALAPPDATA%\RVCStudio\backups\amd_ort_<timestamp>`,
restores the exact previous Qt source and replaced modules, and keeps the
separate ORT Python environment on disk until you choose to remove it.
Do not delete `C:\RVCStudio` or your existing Rust edition to switch engines.

## Technical architecture and safeguards

- Host sounddevice WASAPI input -> bounded callback queue -> single Python
  model inference thread -> bounded callback queue -> virtual microphone output.
- The engine runs ContentVec, RMVPE and RVC ONNX sessions with DirectML EP.
- ORT SessionOptions require `ORT_SEQUENTIAL` and `enable_mem_pattern=False`.
- Same callback never runs ONNX inference directly: GPU pauses cannot block
  the real-time PortAudio callback's Python code.
- Default 400ms chunk and 400ms input context prioritizes speech stability;
  300ms and 200ms experimental presets are available.
- Separate file/audio conversion uses the same ONNX pipeline, not vc-rs wav.
- Models missing sample-rate metadata or incompatible named inputs are rejected
  explicitly to avoid silently producing pitch/time-distorted audio.
- INDEX and Protect are optional, disabled at first run by INDEX Rate 0.
- RNNoise and AP-BWE toggles are disabled: neither was ported to this engine.
- Old Rust SOLA crossfade is **not** reused; model output is cropped to each
  chunk from a rolling input-context window. Quality needs a real listening test.
- Active DirectML EP does not prove every model operator ran on GPU;
  unsupported nodes may execute on CPU. Measure with profiling before release.

## First-use audio test

Set **Pitch 0**, **Index Rate 0**, **Protect 0.50**, **RNNoise off**,
**Net Konuşma 400ms**, select physical mic as input, *different*
virtual audio cable output. Record the same sentence for the Rust engine
and this ONNX Runtime engine. Compare consonants, end-to-end latency and
the worker's `infer_ms`, `processing_ms`, overrun and dropped-sample metrics.

## Actually tested vs not yet validated

Windows GitHub Actions cover:
- Python source syntax and mock model input/output frame geometry;
- real-time chunk lengths, pitch, passthrough and optional INDEX/Protect;
- provider configuration `DmlExecutionProvider` versus CPU-only;
- installability of isolated pip wheels and dependency checks;
- PowerShell installer 5.1 parsing;
- synthetic RVC 1.5.2 Rust hook migration and byte-for-byte rollback.

**Not yet validated**: Actual Radeon 680M/VM inference and latency, real
ContentVec/RMVPE/RVC model files, real microphone and virtual cable streams,
AppSandbox user config, true profile portability and matching NVIDIA sound
quality. CI green is not proof of a fully working production voice changer.
