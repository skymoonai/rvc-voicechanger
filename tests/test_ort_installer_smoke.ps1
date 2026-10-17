# Smoke-test ORT migration from a synthetic existing 1.5.2 Rust AMD hook.
# Safe: changes only RUNNER_TEMP fixture and current CI user's temp settings.
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:RUNNER_TEMP 'RVCStudio_152_Synthetic'
$tools = Join-Path $root 'tools'
New-Item -ItemType Directory -Path $tools -Force | Out-Null
Set-Content -LiteralPath (Join-Path $tools 'rvc_user_state.py') -Encoding UTF8 -Value "APP_VERSION = '1.5.2-beta.1'"
$ui = @'
class Window:
    def __init__(self):
        self.tray = True
    def read(self):
        return (None, {})
    def shutdown(self):
        return None
# RVC_STUDIO_AMD_EDITION_V1_BEGIN
try:
    from tools.rvc_amd_edition import install as install_rust
    install_rust(Window)
except Exception:
    pass
# RVC_STUDIO_AMD_EDITION_V1_END
'@
Set-Content -LiteralPath (Join-Path $tools 'rvc_modern_qt.py') -Encoding UTF8 -Value $ui
$originalHash = (Get-FileHash -LiteralPath (Join-Path $tools 'rvc_modern_qt.py') -Algorithm SHA256).Hash

# Build isolated venv that inherits CI's pre-installed provider wheels.
& python -m venv --system-site-packages (Join-Path $root '.venv-ort-dml')
if ($LASTEXITCODE -ne 0) { throw 'CI virtualenv creation failed.' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PWD 'scripts/install_amd_ort_directml.ps1') -Target $root -SourceRoot $PWD -SkipPackages
if ($LASTEXITCODE -ne 0) { throw 'Synthetic Rust -> ORT migration failed.' }
$patched = Get-Content -LiteralPath (Join-Path $tools 'rvc_modern_qt.py') -Raw -Encoding UTF8
if (!$patched.Contains('RVC_STUDIO_AMD_ORT_DML_V1_BEGIN')) {
    throw 'ORT in-place hook missing after migration.'
}
if ($patched.Contains('RVC_STUDIO_AMD_EDITION_V1_BEGIN')) {
    throw 'Old Rust edition hook was not removed.'
}
foreach ($p in @('rvc_ort_bridge.py', 'rvc_ort_worker.py', 'rvc_ort_edition.py', 'rvc_ort_tasks.py')) {
    if (!(Test-Path (Join-Path $tools $p))) { throw "ORT file not installed: $p" }
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PWD 'scripts/install_amd_ort_directml.ps1') -Target $root -SourceRoot $PWD -Remove
if ($LASTEXITCODE -ne 0) { throw 'Rollback failed.' }
$rollbackHash = (Get-FileHash -LiteralPath (Join-Path $tools 'rvc_modern_qt.py') -Algorithm SHA256).Hash
if ($rollbackHash -ne $originalHash) { throw 'Rollback did not byte-restore the Rust 1.5.2 Qt source.' }
Write-Host 'ORT migration + old Rust hook removal + byte-exact rollback: PASS.'
