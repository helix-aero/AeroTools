# AeroTools: installs the Aero menu in Metashape, and nothing else.
#
#   1. copies the core (menu, update, shared code: about 1 MB) from this folder to %LOCALAPPDATA%\HELIX\AeroToolsApp
#   2. adds the launcher to Metashape's scripts folder, so the Aero menu comes back at each start
#
# The tools are not installed here: in Metashape, Aero > Install plugins... lists them and downloads only the ones you pick.
# Running it again only refreshes the core (installed tools are left alone). A development copy (a .git folder next to
# install\) is used in place, nothing is copied.
#
# Options: -Copy (copy even from a development copy), -InPlace (no copy: the launcher points to this folder),
#          -AppDir <folder> (where to copy, default %LOCALAPPDATA%\HELIX\AeroToolsApp), -Check (reports, changes nothing),
#          -DryRun (shows what would be done), -PauseOnError (waits for a key on failure).
# Messages are ASCII-only: PowerShell 5.1 misreads UTF-8 without a BOM.

param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$AppDir = (Join-Path $env:LOCALAPPDATA 'HELIX\AeroToolsApp'),
    [switch]$Copy,
    [switch]$InPlace,
    [switch]$Check,
    [switch]$DryRun,
    [switch]$PauseOnError
)

$ErrorActionPreference = 'Stop'
$ScriptsDir = Join-Path $env:LOCALAPPDATA 'Agisoft\Metashape Pro\scripts'
$Launcher = Join-Path $ScriptsDir 'aerotools_launcher.py'
$Template = Join-Path $Root 'install\aerotools_launcher.py.tpl'
$ManifestName = 'manifest.json'


function Say($text, $color) {
    if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
}


function Exit-With($code) {
    if ($PauseOnError -and $code -ne 0) {
        Say ''
        Read-Host '  Press Enter to close this window' | Out-Null
    }
    exit $code
}


function Get-Target {
    # The folder the launcher points to: this one for a development copy, otherwise the per-user application folder.
    $dev = Test-Path -LiteralPath (Join-Path $Root '.git')
    if ($InPlace -or ($dev -and -not $Copy)) { return $Root }
    return $AppDir
}


function Test-Core($relative, $packs) {
    # A file belongs to the core when no pack owns it.
    foreach ($pack in $packs) {
        foreach ($prefix in $pack.paths) {
            if ($relative.StartsWith($prefix)) { return $false }
        }
    }
    return $true
}


function Copy-Core($target) {
    $manifestPath = Join-Path $Root $ManifestName
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw "$ManifestName not found in ${Root}: run the installer from the unzipped AeroTools folder." }
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
    $packs = @($manifest.packs)
    $names = @($manifest.files.PSObject.Properties.Name | Where-Object { Test-Core $_ $packs })
    if (-not $names.Count) { throw "$ManifestName lists no core file." }
    $bytes = 0
    foreach ($name in $names) {
        $source = Join-Path $Root ($name -replace '/', '\')
        if (-not (Test-Path -LiteralPath $source)) { throw "Missing file in the installer folder: $name" }
        $bytes += (Get-Item -LiteralPath $source).Length
    }
    Say ("  {0} files, {1:N1} MB -> {2}" -f $names.Count, ($bytes / 1MB), $target)
    if ($DryRun) { return }
    foreach ($name in $names) {
        $source = Join-Path $Root ($name -replace '/', '\')
        $destination = Join-Path $target ($name -replace '/', '\')
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $target $ManifestName) -Force
    Say '  Done: the core is copied.' 'DarkGreen'
}


function Get-LauncherText($target) {
    return [IO.File]::ReadAllText($Template).Replace('@@AEROTOOLS_ROOT@@', $target)
}


function Install-Launcher($target) {
    if (-not (Test-Path -LiteralPath $Template)) { throw "Template not found: $Template" }
    $text = Get-LauncherText $target
    if ((Test-Path -LiteralPath $Launcher) -and ([IO.File]::ReadAllText($Launcher) -ceq $text)) {
        Say '  The Aero menu is already installed and up to date.'
        return
    }
    Say "  Writing $Launcher"
    if ($DryRun) { return }
    New-Item -ItemType Directory -Force -Path $ScriptsDir | Out-Null
    if (Test-Path -LiteralPath $Launcher) { Copy-Item -LiteralPath $Launcher -Destination ($Launcher + '.bak') -Force }
    [IO.File]::WriteAllText($Launcher, $text, (New-Object Text.UTF8Encoding($false)))
    Say '  Done: the Aero menu is installed.' 'DarkGreen'
}


$target = Get-Target

if ($Check) {
    Say ''
    $ok = $true
    if ((Test-Path -LiteralPath $Launcher) -and ([IO.File]::ReadAllText($Launcher) -ceq (Get-LauncherText $target))) {
        Say 'Aero menu : installed'
    } else { Say 'Aero menu : NOT installed'; $ok = $false }
    if (Test-Path -LiteralPath (Join-Path $target 'aerotools\bootstrap.py')) { Say "Core      : $target" } else { Say "Core      : MISSING ($target)"; $ok = $false }
    if ($ok) { exit 0 } else { exit 2 }
}

Say ''
Say '  ============================================================' 'Green'
Say '    AeroTools - installation' 'Green'
Say '  ============================================================' 'Green'

try {
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'aerotools\bootstrap.py'))) {
        throw "aerotools\bootstrap.py not found in ${Root}: run the installer from the unzipped AeroTools folder."
    }
    Say ''
    if ($target -eq $Root) {
        Say '  Step 1 of 2: using this folder in place (development copy).' 'Green'
    } else {
        Say '  Step 1 of 2: copying the Aero core' 'Green'
        Copy-Core $target
    }
    Say ''
    Say '  Step 2 of 2: adding the Aero menu to Metashape' 'Green'
    Install-Launcher $target
    Say ''
    Say '  ============================================================' 'Green'
    Say '    All done. Restart Metashape, then open Aero > Install plugins...' 'Green'
    Say '    to pick the tools you need.' 'Green'
    Say '  ============================================================' 'Green'
    exit 0
} catch {
    Say ''
    Say ('  ERROR: ' + $_.Exception.Message) 'Red'
    Say '  Nothing is lost: run this installer again.' 'Yellow'
    Exit-With 1
}
