# RVC Studio — Source Code / Kaynak Kod

This repository contains the **Python source code** of RVC Studio. The source files
are kept at the project root (`modern_launcher.py`, `realtime_gui.py`, `tools/`,
`infer/`, `i18n/`, `configs/`) because the application imports them using these paths.
There is no need to move them into a separate `src/` folder.

## Download vs. source / İndirme ve kaynak kod

- **End users:** Use the prebuilt Windows installer from [Releases](https://github.com/skymoonai/rvc-voicechanger/releases).
- **Developers:** Clone this repository to inspect, modify or contribute to the Python sources.
- **Requirements:** Python 3.12 x64, Windows 10/11, suitable audio devices and an RVC voice model for conversion. NVIDIA CUDA and DirectML paths have different dependency requirements.

## Developer setup (Windows PowerShell)

```powershell
 git clone https://github.com/skymoonai/rvc-voicechanger.git
 cd rvc-voicechanger
 py -3.12 -m venv .venv
 .\.venv\Scripts\python.exe -m pip install --upgrade pip
 .\.venv\Scripts\python.exe -m pip install -r requirments_cpu_py312.txt
 .\.venv\Scripts\python.exe modern_launcher.py
```

For NVIDIA CUDA, use the corresponding `requirments_cu118_py312.txt` or
`requirments_cu128_py312.txt` file **instead of** the CPU dependency file.
The `requirments_` spelling is retained to match the existing application and installer.

**AMD ROCm (Windows 11) experimental:** See [AMD ROCm Windows guide](docs/AMD_ROCM_WINDOWS.md)
for supported hardware, a separate Python 3.12 environment, GPU checks and the
known untested real-time limitations. This is **not** part of the current installer.

**Important:** This is a **source-only distribution**, not a standalone runnable
bundle. Large HuBERT/RMVPE/AP-BWE weights, user voice models, downloaded model
indices, `deno.exe`, `rnnoise.dll`, installed dependencies and user data are
intentionally excluded. Some features or the `--check` command require these
optional assets. Obtain needed binaries and model weights from their official
upstream projects and check their redistribution terms, or use the published
installer for an end-user installation. The full source setup has **not** been
runtime-tested as a clean install in this packaging pass.

## What's included / Neler dahil?

- Main entry points: `modern_launcher.py`, `realtime_gui.py`
- RVC inference: `infer/`, `configs/config.py`, `configs/v1/`, `configs/v2/`
- UI, Soundpad, audio processing, library, hotkeys, profiles and helpers: `tools/`
- Localization: `i18n/`, `assets/locales/`
- Non-personal resource/configuration templates, branding and license notices: `assets/`
- Windows dependency lists: `requirments_*_py312.txt`

## Privacy and source packaging / Gizlilik

The following **must not be committed**: `configs/config.json`,
`configs/model_profiles.json`, `configs/hotkeys.json`, audio recordings,
Soundpad user data, local logs, `.venv/`, caches, personal model files,
update backups, signing private keys or credentials.

The runtime creates some settings locally; missing personal configuration files
are intentional. **Do not copy your entire installed program directory into
GitHub.** See `.gitignore` for exclusions.

## Licenses / Lisanslar

RVC Studio's own MIT license is in [`LICENSE`](LICENSE). Some files derive from
third-party open-source projects and retain their respective copyright and
license obligations. See [`RVC_UPSTREAM_LICENSE.txt`](RVC_UPSTREAM_LICENSE.txt),
[`THIRD_PARTY_NOTICES.txt`](THIRD_PARTY_NOTICES.txt) and the license files under
`assets/`. Source publication does not grant rights to distribute third-party
voice models or weights. Review third-party provenance and attribution before
redistributing or commercializing a modified build.

## Contributing

Bug reports and pull requests are welcome. Do not commit secrets, voice samples
without permission, or copyrighted model weights. Keep changes focused and
include reproduction steps for audio-device issues.
