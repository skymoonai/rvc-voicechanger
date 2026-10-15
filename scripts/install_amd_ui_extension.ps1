# Install the experimental AMD tab into an existing RVC Studio 1.5.2 UI.
# Crucially, NEVER replace tools/rvc_modern_qt.py with the old 1.3 branch file.
# SourceRoot must be the checked-out feature/amd-vcrs-preview tree.
[CmdletBinding()]
param(
    [string]$Target = 'C:\RVCStudio',
    [string]$SourceRoot = '',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
if (-not $SourceRoot) {
    if (-not $PSCommandPath) { throw 'Pass -SourceRoot pointing to the AMD branch checkout.' }
    $SourceRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
}
$Target = [IO.Path]::GetFullPath($Target)
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$tools = Join-Path $Target 'tools'
$ui = Join-Path $tools 'rvc_modern_qt.py'
$versionFile = Join-Path $tools 'rvc_user_state.py'
$hookStart = '# RVC_STUDIO_AMD_EXTENSION_V2_BEGIN'
$hookEnd = '# RVC_STUDIO_AMD_EXTENSION_V2_END'
if (!(Test-Path -LiteralPath $ui -PathType Leaf)) {
    throw "RVC Studio UI missing at $ui. Install RVC Studio 1.5.2 first."
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
$old = [IO.File]::ReadAllText($ui, [Text.Encoding]::UTF8)
$pattern = '(?ms)^# RVC_STUDIO_AMD_EXTENSION_V2_BEGIN\r?\n.*?^# RVC_STUDIO_AMD_EXTENSION_V2_END\r?\n?'
if ($Remove) {
    if ($old.Contains($hookStart)) {
        $new = [regex]::Replace($old, $pattern, '')
        if ($new -eq $old) { throw 'AMD extension marker was incomplete; no changes made.' }
        [IO.File]::WriteAllText($ui, $new, $utf8)
        Write-Host 'AMD tab startup hook removed. Original UI retained.'
    } else {
        Write-Host 'AMD tab startup hook was not installed.'
    }
    exit 0
}
if (!(Test-Path -LiteralPath $versionFile)) {
    throw 'RVC Studio version metadata missing. Aborting without modifying the UI.'
}
$versionCode = [IO.File]::ReadAllText($versionFile, [Text.Encoding]::UTF8)
$versionMatch = [regex]::Match($versionCode, '(?m)^APP_VERSION\s*=\s*[\x27\x22]([^\x27\x22]+)[\x27\x22]')
if (!$versionMatch.Success) { throw 'Cannot identify RVC Studio installed version. No changes made.' }
$version = $versionMatch.Groups[1].Value
if (!$version.StartsWith('1.5.2')) {
    throw "This extension is intended for RVC Studio 1.5.2 (found: $version). Do not mix versions."
}
# The old 1.3 codebase has no system tray initialization. Refuse to patch it.
if ($old -notmatch '(?i)\btray\b') {
    throw 'Installed UI has no system-tray implementation. Restore the official 1.5.2 setup first.'
}
if ($old.Contains($hookStart)) {
    Write-Host 'AMD hook is already installed. Updating only AMD extension modules.'
}

$required = @('rvc_amd_bridge.py', 'rvc_amd_extension.py')
foreach ($file in $required) {
    if (!(Test-Path -LiteralPath (Join-Path (Join-Path $SourceRoot 'tools') $file))) {
        throw "AMD extension source missing: $file. Download the full feature/amd-vcrs-preview ZIP."
    }
}
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path (Join-Path $env:LOCALAPPDATA 'RVCStudio\backups') "amd_ui_$timestamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item -LiteralPath $ui -Destination (Join-Path $backup 'rvc_modern_qt.py') -Force
$originalModules = @{}
foreach ($file in $required) {
    $p = Join-Path $tools $file
    $originalModules[$file] = Test-Path -LiteralPath $p
    if ($originalModules[$file]) {
        Copy-Item -LiteralPath $p -Destination (Join-Path $backup $file) -Force
    }
}
try {
    foreach ($file in $required) {
        $sourceFile = Join-Path (Join-Path $SourceRoot 'tools') $file
        $targetFile = Join-Path $tools $file
        if (![string]::Equals([IO.Path]::GetFullPath($sourceFile), [IO.Path]::GetFullPath($targetFile), [StringComparison]::OrdinalIgnoreCase)) {
            Copy-Item -LiteralPath $sourceFile -Destination $targetFile -Force
        }
    }
    if (!$old.Contains($hookStart)) {
        $hook = @'

# RVC_STUDIO_AMD_EXTENSION_V2_BEGIN
# Optional compatibility extension; the installed UI and tray stay untouched.
try:
    from tools.rvc_amd_extension import install as _install_amd_vcrs_extension
    _install_amd_vcrs_extension(Window)
except Exception as _amd_extension_error:
    print("AMD extension initialization failed: " + str(_amd_extension_error), flush=True)
# RVC_STUDIO_AMD_EXTENSION_V2_END
'@
        [IO.File]::WriteAllText($ui, ($old.TrimEnd([char[]]@(13, 10)) + "`r`n" + $hook + "`r`n"), $utf8)
    }
    # Parse, do not import or execute the application in this step.
    $python = Join-Path $Target '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $python)) { $python = 'python' }
    $check = @'
import ast
import pathlib
import sys
for file in sys.argv[1:]:
    ast.parse(pathlib.Path(file).read_text(encoding="utf-8-sig"), filename=file)
print("AMD extension: syntax OK")
'@
    $check | & $python - $ui (Join-Path $tools 'rvc_amd_extension.py') (Join-Path $tools 'rvc_amd_bridge.py')
    if ($LASTEXITCODE -ne 0) { throw "Python AST validation failed; restoring original UI." }
    Write-Host "AMD extension installed into RVC Studio $version"
    Write-Host "Backup: $backup"
    Write-Host 'Restart RVC Studio. The AMD Motoru tab will appear without replacing the installed tray UI.'
    Write-Host 'To uninstall: run this script with -Remove.'
}
catch {
    Copy-Item -LiteralPath (Join-Path $backup 'rvc_modern_qt.py') -Destination $ui -Force
    foreach ($file in $required) {
        $original = Join-Path $backup $file
        $p = Join-Path $tools $file
        if ($originalModules[$file]) { Copy-Item -LiteralPath $original -Destination $p -Force }
        elseif (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
    }
    throw
}
