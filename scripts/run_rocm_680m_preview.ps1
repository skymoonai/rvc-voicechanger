# Launch original RVC Studio Python GUI with experimental TheRock ROCm HIP.
# The Rust vc-rs/ONNX edition is not loaded. Models remain PTH/INDEX.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if (-not $PSCommandPath) { throw 'Save and execute the .ps1 script.' }
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$python = Join-Path $root '.venv-rocm-gfx1035\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw ('ROCm gfx1035 environment not installed. Run: powershell -ExecutionPolicy Bypass -File "' +
        (Join-Path $root 'scripts/setup_rocm_680m_preview.ps1') + '" -AcceptNightlyRisk')
}
Push-Location $root
try {
    $env:RVC_RUNTIME_PROFILE = 'rocm'
    $env:RVC_CUDA_GRAPH = '0'
    $env:RVC_ROCM_REQUIRE_GFX = 'gfx1035'
    $env:PYTHONUTF8 = '1'
    & $python 'scripts/check_rocm.py'
    if ($LASTEXITCODE -ne 0) {
        throw 'Real Radeon 680M HIP GPU test failed; refusing to fall back silently to CPU. AppSandbox GPU-PV may not expose HIP runtime.'
    }
    & $python 'modern_launcher.py'
    if ($LASTEXITCODE -ne 0) {
        throw ("RVC Studio ROCm launcher ended with code " + $LASTEXITCODE + '. See startup logs.')
    }
}
finally {
    Pop-Location
}
