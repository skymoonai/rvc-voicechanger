# One-click migration for installed RVC Studio 1.5.2 + existing Rust AMD Edition.
# HTTPS GitHub archive -> local installer, NOT a remote script piped into iex.
[CmdletBinding()]
param(
    [string]$Target = 'C:\RVCStudio',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$store = Join-Path $env:LOCALAPPDATA 'RVCStudio\installer\amd-ort-directml'
$installerName = 'install_amd_ort_directml.ps1'
if ($Remove) {
    $installer = Get-ChildItem -LiteralPath $store -Recurse -Filter $installerName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (!$installer) {
        throw "Local rollback installer not found in $store. Download the project ZIP and run scripts/install_amd_ort_directml.ps1 -Remove."
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer.FullName -Target $Target -Remove
    if ($LASTEXITCODE -ne 0) { throw 'ONNX Runtime rollback failed.' }
    exit 0
}
if (!(Test-Path -LiteralPath (Join-Path $Target 'tools\rvc_modern_qt.py'))) {
    throw "RVC Studio was not detected in $Target. Pass -Target to point at your installed 1.5.2."
}
New-Item -ItemType Directory -Path $store -Force | Out-Null
$zip = Join-Path $env:TEMP 'RVCStudio-ONNX-DirectML-edition.zip'
$uri = 'https://github.com/skymoonai/rvc-voicechanger/archive/refs/heads/feature/amd-onnxruntime-directml.zip'
Write-Host 'Downloading isolated ONNX Runtime DirectML edition source...' -ForegroundColor Cyan
Invoke-WebRequest -Uri $uri -OutFile $zip -UseBasicParsing
if (!(Test-Path -LiteralPath $zip) -or (Get-Item $zip).Length -lt 10240) {
    throw 'The downloaded GitHub archive is empty or unexpectedly small.'
}
$directory = Join-Path $store 'checkout'
if (Test-Path -LiteralPath $directory) { Remove-Item -LiteralPath $directory -Recurse -Force }
Expand-Archive -LiteralPath $zip -DestinationPath $directory -Force
$installer = Get-ChildItem -LiteralPath $directory -Recurse -Filter $installerName |
    Select-Object -First 1
if (!$installer) { throw 'Expected migration installer missing in the downloaded project.' }
$sourceRoot = Split-Path -Parent (Split-Path -Parent $installer.FullName)
Write-Host 'Applying ONNX Runtime migration to existing AMD Edition...'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer.FullName -Target $Target -SourceRoot $sourceRoot
if ($LASTEXITCODE -ne 0) {
    throw "Installation failed. Your old AMD UI should remain unchanged. The error is above."
}
Write-Host 'Done. Restart RVC Studio. Rust vc-rs audio process is no longer used.' -ForegroundColor Green
Write-Host "Rollback (from this one-click script): -Remove -Target '$Target'"
