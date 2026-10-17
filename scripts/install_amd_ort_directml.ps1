# RVC Studio 1.5.2 AMD ONNX Runtime DirectML migration.
# Existing NVIDIA/PyTorch and Rust vc-rs files are not overwritten.
[CmdletBinding()]
param(
    [string]$Target = 'C:\RVCStudio',
    [string]$SourceRoot = '',
    [switch]$Remove,
    [switch]$SkipPackages
)
$ErrorActionPreference = 'Stop'
$Target = [IO.Path]::GetFullPath($Target)
if (-not $SourceRoot) { $SourceRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath) }
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$toolDir = Join-Path $Target 'tools'
$ui = Join-Path $toolDir 'rvc_modern_qt.py'
$settings = Join-Path $env:LOCALAPPDATA 'RVCStudio'
$state = Join-Path $settings 'amd_ort_rollback.json'
$required = @(
  'rvc_ort_edition.py', 'rvc_ort_bridge.py', 'rvc_ort_worker.py', 'rvc_ort_tasks.py',
  'rvc_amd_assets.py', 'rvc_amd_monitor.py', 'rvc_amd_soundpad.py',
  'rvc_amd_quality.py', 'rvc_amd_metrics.py', 'rvc_amd_convert.py'
)
$utf8 = New-Object System.Text.UTF8Encoding($false)
if (!(Test-Path -LiteralPath $ui -PathType Leaf)) {
    throw "RVC Studio 1.5.2 Qt UI bulunamadi: $ui"
}
if ($Remove) {
    if (!(Test-Path -LiteralPath $state)) { throw 'No ORT rollback backup recorded.' }
    $info = Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json
    if (!(Test-Path -LiteralPath $info.backup)) { throw 'Rollback backup is missing.' }
    Copy-Item -LiteralPath (Join-Path $info.backup 'rvc_modern_qt.py') -Destination $ui -Force
    foreach ($name in $required) {
        $before = Join-Path $info.backup $name
        $to = Join-Path $toolDir $name
        if (Test-Path -LiteralPath $before) {
            Copy-Item -LiteralPath $before -Destination $to -Force
        } elseif (Test-Path -LiteralPath $to) {
            Remove-Item -LiteralPath $to -Force
        }
    }
    Remove-Item -LiteralPath $state -Force
    Write-Host 'Onceki AMD surumu ve 1.5.2 arayuzu geri yuklendi.' -ForegroundColor Green
    exit 0
}
$versionFile = Join-Path $toolDir 'rvc_user_state.py'
if (!(Test-Path -LiteralPath $versionFile)) { throw 'Installed version metadata missing.' }
$v = [IO.File]::ReadAllText($versionFile, [Text.Encoding]::UTF8)
$match = [regex]::Match($v, '(?m)^APP_VERSION\s*=\s*[\x27\x22]([^\x27\x22]+)[\x27\x22]')
if (!$match.Success -or !$match.Groups[1].Value.StartsWith('1.5.2')) {
    throw "RVC Studio 1.5.2 required. Found: $($match.Groups[1].Value)"
}
$original = [IO.File]::ReadAllText($ui, [Text.Encoding]::UTF8)
if ($original -notmatch '(?i)\btray\b') {
    throw 'RVC Studio 1.5.2 system-tray UI was not found. No changes made.'
}
if (Test-Path -LiteralPath $state) {
    throw 'ORT DirectML already installed; use -Remove before installing again.'
}
foreach ($name in $required) {
    if (!(Test-Path -LiteralPath (Join-Path (Join-Path $SourceRoot 'tools') $name))) {
        throw "Incomplete project ZIP; missing: $name"
    }
}

# Isolate ONNX Runtime dependencies from installed CUDA/torch packages.
$envPath = Join-Path $Target '.venv-ort-dml'
$python = Join-Path $envPath 'Scripts\python.exe'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    $candidates = @(
        (Join-Path $Target '.venv\Scripts\python.exe'),
        (Join-Path $Target 'python\python.exe'),
        (Join-Path $Target 'runtime\python.exe')
    )
    $base = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($base) {
        & $base -m venv $envPath
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv $envPath
        if ($LASTEXITCODE -ne 0) { & py -3.10 -m venv $envPath }
    } else {
        throw 'Python 3.10 or 3.12 is required. Existing AMD application unchanged.'
    }
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Cannot create isolated ONNX Runtime Python environment.'
    }
}
if (-not $SkipPackages) {
    Write-Host 'Installing Python ONNX Runtime DirectML in a separate venv...' -ForegroundColor Cyan
    & $python -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip installation failed; current app untouched.' }
    & $python -m pip install --disable-pip-version-check 'onnxruntime-directml==1.24.2' 'numpy>=1.26.4,<2' 'scipy>=1.11,<1.16' 'sounddevice>=0.5,<1' 'soundfile>=0.12,<1' 'faiss-cpu>=1.8,<2'
    if ($LASTEXITCODE -ne 0) { throw 'DirectML dependency installation failed; old AMD UI unchanged.' }
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'DirectML dependency conflicts detected; old AMD UI unchanged.' }
}
$smoke = @'
import onnxruntime as ort, numpy, scipy, sounddevice, soundfile
print('ONNX Runtime:', ort.__version__)
print('Execution providers:', ort.get_available_providers())
assert 'DmlExecutionProvider' in ort.get_available_providers(), 'No DirectML EP'
'@
$smoke | & $python -
if ($LASTEXITCODE -ne 0) {
    throw 'DirectML preflight failed. Existing Rust AMD installation is unchanged.'
}
$astCheck = @'
import ast, pathlib, sys
for p in sys.argv[1:]:
    ast.parse(pathlib.Path(p).read_text(encoding='utf-8-sig'), filename=p)
print('ONNX Runtime Python source syntax: OK')
'@
$sourceFiles = @($required | ForEach-Object { Join-Path (Join-Path $SourceRoot 'tools') $_ })
$astCheck | & $python - @sourceFiles
if ($LASTEXITCODE -ne 0) { throw 'New AMD Python source invalid. Old installation unchanged.' }

New-Item -ItemType Directory -Path $settings -Force | Out-Null
$backup = Join-Path $settings ("backups\amd_ort_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item -LiteralPath $ui -Destination (Join-Path $backup 'rvc_modern_qt.py') -Force
foreach ($name in $required) {
    $before = Join-Path $toolDir $name
    if (Test-Path -LiteralPath $before) {
        Copy-Item -LiteralPath $before -Destination (Join-Path $backup $name) -Force
    }
}

# Unhook vc-rs Rust engine. Original installed 1.5.2 window/tray is untouched.
$patched = $original
$patterns = @(
  '(?ms)^# RVC_STUDIO_AMD_EXTENSION_V2_BEGIN\r?\n.*?^# RVC_STUDIO_AMD_EXTENSION_V2_END\r?\n?',
  '(?ms)^# RVC_STUDIO_AMD_EDITION_V1_BEGIN\r?\n.*?^# RVC_STUDIO_AMD_EDITION_V1_END\r?\n?',
  '(?ms)^# RVC_STUDIO_AMD_ORT_DML_V1_BEGIN\r?\n.*?^# RVC_STUDIO_AMD_ORT_DML_V1_END\r?\n?'
)
foreach ($pattern in $patterns) { $patched = [regex]::Replace($patched, $pattern, '') }
$hook = @'

# RVC_STUDIO_AMD_ORT_DML_V1_BEGIN
# Python ONNX Runtime DirectML inference. No Rust vc-rs audio subprocess.
try:
    from tools.rvc_ort_edition import install as _install_amd_ort
    _install_amd_ort(Window)
except Exception as _ort_install_error:
    print('[ORT DirectML] Init error: ' + repr(_ort_install_error), flush=True)
# RVC_STUDIO_AMD_ORT_DML_V1_END
'@
$patched = $patched.TrimEnd([char[]]@(13, 10)) + [char]13 + [char]10 + $hook + [char]13 + [char]10
try {
    foreach ($name in $required) {
        Copy-Item -LiteralPath (Join-Path (Join-Path $SourceRoot 'tools') $name) -Destination (Join-Path $toolDir $name) -Force
    }
    [IO.File]::WriteAllText($ui, $patched, $utf8)
    $installedFiles = @($required | ForEach-Object { Join-Path $toolDir $_ })
    $astCheck | & $python - $ui @installedFiles
    if ($LASTEXITCODE -ne 0) { throw 'Installed Qt UI syntax check failed.' }
    $manifest = @{ target = $Target; backup = $backup; version = $match.Groups[1].Value }
    [IO.File]::WriteAllText($state, ($manifest | ConvertTo-Json), $utf8)
    Write-Host 'ONNX Runtime DirectML Edition installed. Restart RVC Studio.' -ForegroundColor Green
    Write-Host 'Rust vc-rs inference is unhooked. Native PTH exporter may remain as a converter only.'
    Write-Host "Rollback backup: $backup"
    Write-Host 'Rollback with this installer -Remove option.'
}
catch {
    Copy-Item -LiteralPath (Join-Path $backup 'rvc_modern_qt.py') -Destination $ui -Force
    foreach ($name in $required) {
        $before = Join-Path $backup $name
        $to = Join-Path $toolDir $name
        if (Test-Path -LiteralPath $before) {
            Copy-Item -LiteralPath $before -Destination $to -Force
        } elseif (Test-Path -LiteralPath $to) {
            Remove-Item -LiteralPath $to -Force
        }
    }
    throw
}
