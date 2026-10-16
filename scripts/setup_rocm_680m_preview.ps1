# RVC Studio experimental ROCm GPU driver for Radeon 680M / gfx1035.
# This is a separate preview environment, NOT an updater for C:\RVCStudio.
# Uses TheRock *nightly* packages; releases can change or break without notice.
# Native Windows HIP in a GPU-PV AppSandbox VM is not guaranteed to function.
[CmdletBinding()]
param(
    [switch]$AcceptNightlyRisk,
    [switch]$AllowUnrecognizedAdapter,
    [switch]$SkipInstall,
    [string]$TargetGfx = 'gfx1035'
)
$ErrorActionPreference = 'Stop'
if (-not $AcceptNightlyRisk) {
    throw 'Experimental TheRock Windows packages require explicit -AcceptNightlyRisk. Read docs/AMD_ROCM_680M_PREVIEW.md first.'
}
if ($TargetGfx -ne 'gfx1035') {
    throw 'This install profile is only validated for selecting the gfx1035 wheel. Do not guess another target.'
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This profile targets native Windows 11 only.'
}
$os = Get-CimInstance Win32_OperatingSystem
if ([int]$os.BuildNumber -lt 22000) {
    throw 'This ROCm experiment requires Windows 11.'
}
if ([string]::IsNullOrEmpty($PSCommandPath)) {
    throw 'Could not resolve script location. Run this saved .ps1 file, not its extracted fragments.'
}
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$gpu = @((Get-CimInstance Win32_VideoController | ForEach-Object Name) | Where-Object { $_ })
$cpu = @((Get-CimInstance Win32_Processor | ForEach-Object Name) | Where-Object { $_ })
Write-Host ('CPU: ' + ($cpu -join '; '))
Write-Host ('Display adapters: ' + ($gpu -join '; '))
if (-not @($gpu | Where-Object { $_ -match '(?i)AMD|Radeon' }).Count) {
    throw 'No AMD adapter reported. ROCm cannot be assumed just because a VM emulates a display device.'
}
# Radeon 680M may show only "AMD Radeon(TM) Graphics" in AppSandbox, so
# permit that ONLY via explicit -AllowUnrecognizedAdapter. The REAL HIP test
# below is mandatory and never overridden.
if ($cpu -notmatch '(?i)7735HS|6800H|6800U|6900HS|6900HX|6850U' -and
    $gpu -notmatch '(?i)680M' -and -not $AllowUnrecognizedAdapter) {
    throw 'Could not identify Radeon 680M/gfx1035. For generic AppSandbox AMD Radeon(TM) Graphics use -AllowUnrecognizedAdapter, but HIP computation must still pass.'
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python Windows Launcher is missing. Install Python 3.12 x64 first.'
}
& py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32"
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.12 x64 is required.'
}
$venv = Join-Path $root '.venv-rocm-gfx1035'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3.12 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create isolated ROCm gfx1035 venv.' }
}
function Invoke-Python {
    param([string[]]$Parameters)
    & $python @Parameters
    if ($LASTEXITCODE -ne 0) { throw ('Python command failed (' + $LASTEXITCODE + '): ' + ($Parameters -join ' ')) }
}
Set-Location -LiteralPath $root
if (-not $SkipInstall) {
    Write-Host 'Installing application dependencies in its separate .venv-rocm-gfx1035, NOT the NVIDIA/AMD/RVC Studio base folder.'
    Invoke-Python @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools>=75,<81', 'wheel')
    Invoke-Python @('-m', 'pip', 'install', '-r', 'requirments_rocm_py312.txt')
    # Explicitly allow TheRock pre-release wheel packages for the *specific*
    # iGPU gfx1035. Do not automatically modify the Windows display driver.
    # No gfx override or fake CUDA fallback. The PyTorch wheel may not be
    # published for every Windows/nightly date, in which case setup fails.
    $index = 'https://nightly.repo.amd.com/rocm/whl-next/'
    Write-Warning 'Installing large, experimental AMD TheRock ROCm/PyTorch Windows nightly wheels. They may be unstable or unavailable.'
    Invoke-Python @(
        '-m', 'pip', 'install', '--pre',
        '--index-url', $index,
        'torch[device-gfx1035]',
        'torchvision[device-gfx1035]',
        'torchaudio'
    )
    Invoke-Python @('-m', 'pip', 'check')
}
$env:RVC_RUNTIME_PROFILE = 'rocm'
$env:RVC_CUDA_GRAPH = '0'
$env:RVC_ROCM_REQUIRE_GFX = 'gfx1035'
Write-Host 'Running mandatory GPU compute / architecture preflight.'
Invoke-Python @('scripts/check_rocm.py')
Write-Host ''
Write-Host 'ROCm gfx1035 preflight passed. RVC Studio voice model inference is STILL unverified.' -ForegroundColor Green
Write-Host 'Start (from this source checkout): powershell.exe -ExecutionPolicy Bypass -File .\scripts\run_rocm_680m_preview.ps1'
