# Experimental AMD ROCm 7.2.1 environment for RVC Studio on Windows 11.
# Keep this separate from the portable installer and DirectML/CUDA environments.
param(
    [switch]$AllowUnsupportedGpu
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This ROCm installation profile requires Windows 11.'
}
$os = Get-CimInstance Win32_OperatingSystem
if ([int]$os.BuildNumber -lt 22000) {
    throw 'AMD ROCm 7.2.1 PyTorch support requires Windows 11. Use the existing DirectML profile on Windows 10.'
}

$gpuNames = @((Get-CimInstance Win32_VideoController | ForEach-Object Name) | Where-Object { $_ })
$cpuNames = @((Get-CimInstance Win32_Processor | ForEach-Object Name) | Where-Object { $_ })
$amdAdapters = @($gpuNames | Where-Object { $_ -match '(?i)AMD|Radeon' })
if ($amdAdapters.Count -eq 0) {
    throw 'No AMD graphics adapter was reported by Windows. This script only targets AMD ROCm.'
}
Write-Host ('Detected graphics adapters: ' + ($gpuNames -join ', '))

# This is a conservative hint, not a substitute for the current AMD support table.
$knownRadeon = @($amdAdapters | Where-Object {
    $_ -match '(?i)RX\s*(9070\s*XT|9070|9060\s*XT|7900\s*XTX|7700)\b|\bPRO\s*W7900\b|\bAI\s*PRO\s*R9700\b'
})
$knownRyzen = @($cpuNames | Where-Object {
    $_ -match '(?i)Ryzen\s+AI\s+(Max\+?\s+(395|390|385)|9\s+(HX\s+)?(375|370|365|475|470|465))\b'
})
if ($knownRadeon.Count -eq 0 -and $knownRyzen.Count -eq 0 -and -not $AllowUnsupportedGpu) {
    throw ('GPU/APU not on the tested ROCm 7.2.1 Windows list. See docs/AMD_ROCM_WINDOWS.md. ' +
           'Only if you accept an unsupported experiment, rerun with -AllowUnsupportedGpu.')
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python Launcher (py) is missing. Install Python 3.12 x64 with the Windows launcher.'
}
& py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.12 x64 is required. Try: py -3.12 -V'
}

$venv = Join-Path $root '.venv-rocm'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) {
    & py -3.12 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create isolated .venv-rocm environment.' }
}

function Invoke-Python {
    param([string[]]$Arguments)
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ('Python command failed with exit code ' + $LASTEXITCODE + ': ' + ($Arguments -join ' '))
    }
}

Write-Host 'Installing RVC Studio dependencies into the isolated ROCm environment.'
Invoke-Python -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools>=75,<81', 'wheel')
Invoke-Python -Arguments @('-m', 'pip', 'install', '-r', 'requirments_rocm_py312.txt')

Write-Host 'Installing AMD ROCm 7.2.1 components from official AMD package URLs.'
$base = 'https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1'
$rocmPackages = @(
    "$base/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl",
    "$base/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
    "$base/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl",
    "$base/rocm-7.2.1.tar.gz"
)
Invoke-Python -Arguments (@('-m', 'pip', 'install', '--no-cache-dir') + $rocmPackages)

# Install AMD torch packages last so transitive dependencies cannot replace them
# with incompatible CPU/NVIDIA PyTorch builds.
$torchPackages = @(
    "$base/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl",
    "$base/torchaudio-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl",
    "$base/torchvision-0.24.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"
)
Invoke-Python -Arguments (@('-m', 'pip', 'install', '--no-cache-dir') + $torchPackages)
Invoke-Python -Arguments @('-m', 'pip', 'check')
Invoke-Python -Arguments @('scripts/check_rocm.py')

Write-Host ''
Write-Host 'ROCm environment installed and basic GPU compute verification passed.' -ForegroundColor Green
Write-Host 'To start RVC Studio with ROCm, run:'
Write-Host '  powershell -ExecutionPolicy Bypass -File .\scripts\run_rocm_windows.ps1'
Write-Warning 'A successful tensor probe does not prove real-time audio inference works. Test an actual model and audio device.'
