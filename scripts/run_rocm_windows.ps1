# Start RVC Studio in the isolated experimental AMD ROCm environment.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv-rocm\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'ROCm environment missing. Run scripts/setup_rocm_windows.ps1 first.'
}
Set-Location $root
$env:RVC_RUNTIME_PROFILE = 'rocm'
$env:RVC_CUDA_GRAPH = '0'
& $python 'scripts/check_rocm.py'
if ($LASTEXITCODE -ne 0) { throw 'ROCm GPU preflight failed; nothing was launched.' }
& $python 'modern_launcher.py'
exit $LASTEXITCODE
