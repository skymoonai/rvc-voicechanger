# Installs the pinned vc-rs Windows ML GUI+CLI runtime beside RVC Studio.
# Run from PowerShell. This does not alter Python/PyTorch or the NVIDIA install.
[CmdletBinding()]
param(
    [string]$Destination = ''
)
$ErrorActionPreference = 'Stop'
# PowerShell may not populate $PSScriptRoot when evaluating parameter defaults.
# Resolve the destination only after entering the script, when $PSCommandPath
# points to this file (independent of the caller's current working directory).
if ([string]::IsNullOrWhiteSpace($Destination)) {
    if ([string]::IsNullOrWhiteSpace($PSCommandPath)) {
        throw 'Cannot determine installer location; please supply -Destination explicitly.'
    }
    $scriptFolder = Split-Path -Parent $PSCommandPath
    $studioFolder = Split-Path -Parent $scriptFolder
    $Destination = Join-Path $studioFolder 'engines\vc-rs'
}
$tag = 'v0.5.3'
$name = "vc-rs-windowsml-$tag-win-x64.zip"
$url = "https://github.com/shirohata/vc-rs/releases/download/$tag/$name"
$sha256 = '3f263b53715572aa0c5f6b6b30f0acea672de3c3d04f200125bd8c34cb67e758'
$destination = [IO.Path]::GetFullPath($Destination)
$temp = Join-Path ([IO.Path]::GetTempPath()) ("rvcstudio-vcrs-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
try {
    $zip = Join-Path $temp $name
    Write-Host "Downloading $tag from shirohata/vc-rs..."
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $sha256) {
        throw "vc-rs SHA256 mismatch. Refusing to unpack! expected=$sha256 actual=$actual"
    }
    $extract = Join-Path $temp 'unpack'
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $engine = Get-ChildItem -LiteralPath $extract -Filter 'vc-rs.exe' -File -Recurse |
        Select-Object -First 1
    $gui = Get-ChildItem -LiteralPath $extract -Filter 'vc-gui.exe' -File -Recurse |
        Select-Object -First 1
    if (-not $engine -or -not $gui) {
        throw 'Invalid vc-rs archive: expected vc-rs.exe and vc-gui.exe'
    }
    # Copy from the containing folder, preserving model/runtime DLL layout.
    $sourceDir = $engine.Directory.FullName
    if ($gui.Directory.FullName -ne $sourceDir) {
        throw 'Unexpected vc-rs archive layout: the GUI and CLI are not adjacent'
    }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -Path (Join-Path $sourceDir '*') -Destination $destination -Recurse -Force
    Write-Host "vc-rs $tag installed: $destination"
    Write-Host 'RVC Studio > AMD Motoru > PTH -> ONNX donusturucuyu ac'
    Write-Host 'Download the ContentVec and RMVPE models in vc-rs GUI before your first run.'
    Write-Host 'Windows App SDK Runtime 2.1+ is required for Windows ML and is NOT installed by this script.'
    Write-Host 'Official runtime: https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads'
}
finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
