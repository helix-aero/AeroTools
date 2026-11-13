# AeroTools: installs the Aero menu in Metashape, and nothing else.
#
#   1. copies the core (menu, update, shared code: about 1 MB on the stable version, about 26 MB on the dev version) from this folder
#      to %LOCALAPPDATA%\HELIX\AeroToolsApp
#   2. adds the launcher to Metashape's scripts folder, so the Aero menu comes back at each start
#
# The tools are not installed here: in Metashape, Aero > Install plugins... lists them and downloads only the ones you pick.
# Running it again only refreshes the core (installed tools are left alone). A development copy (a .git folder next to
# install\) is used in place, nothing is copied.
#
# The copy is safe: the files go to a staging folder next to the target first (AppDir.new) and are checked against the SHA-256 digests of
# manifest.json; only then are they moved into place. The files they replace are kept in AppDir.old (one generation) and put back if a
# move fails. settings.json is never read or written here.
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
$ManifestName = 'manifest.json'
$TextExtensions = @('.py', '.md', '.ps1', '.bat', '.tpl', '.json', '.txt', '.yml', '.yaml', '.cfg', '.toml', '.svg', '.html', '.css', '.js', '.csv', '.ini', '.geojson')


function Format-Folder($path) {
    # No trailing backslash (a quoted "D:\" argument would also swallow its closing quote), except for a drive root.
    if (-not $path) { return $path }
    $full = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($path))
    $drive = [IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $drive.Length) { $full = $full.TrimEnd('\') }
    return $full
}


$Root = Format-Folder $Root
$AppDir = Format-Folder $AppDir
$Template = Join-Path $Root 'install\aerotools_launcher.py.tpl'


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
            if ($relative.StartsWith($prefix, [StringComparison]::Ordinal)) { return $false }
        }
    }
    return $true
}


function Get-Digest($path, $name) {
    # Same rule as aerotools/updater.py: SHA-256, line endings normalised (CRLF -> LF) for a text file.
    $bytes = [IO.File]::ReadAllBytes($path)
    $extension = [IO.Path]::GetExtension($name).ToLowerInvariant()
    if (($TextExtensions -contains $extension) -or ([IO.Path]::GetFileName($name) -eq 'LICENSE')) {
        $latin = [Text.Encoding]::GetEncoding(28591)
        $bytes = $latin.GetBytes($latin.GetString($bytes).Replace("`r`n", "`n"))
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant() } finally { $sha.Dispose() }
}


function Remove-Folder($path) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue }
}


function Move-Aside($destination, $backup, $moves) {
    # Moves a file of the target to the backup folder and records it, so that a failure can put it back.
    $saved = $null
    if (Test-Path -LiteralPath $destination) {
        $saved = $destination.Replace($script:CurrentTarget, $backup)
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $saved) | Out-Null
        Move-Item -LiteralPath $destination -Destination $saved -Force
    }
    [void]$moves.Add(@($destination, $saved))
}


function Copy-Core($target) {
    $manifestPath = Join-Path $Root $ManifestName
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw "$ManifestName not found in ${Root}: run the installer from the unzipped AeroTools folder." }
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
    $packs = @($manifest.packs)
    $names = @($manifest.files.PSObject.Properties.Name | Where-Object { Test-Core $_ $packs })
    if (-not $names.Count) { throw "$ManifestName lists no core file." }
    # Every missing file is reported at once. A missing guide is only a warning; a missing code file stops the installation.
    $missing = @($names | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Root ($_ -replace '/', '\'))) })
    $fatal = @($missing | Where-Object { -not $_.StartsWith('docs/', [StringComparison]::Ordinal) })
    if ($fatal.Count) {
        $shown = ($fatal | Select-Object -First 5) -join ', '
        throw "$($fatal.Count) $(if ($fatal.Count -eq 1) { 'file' } else { 'files' }) missing in the installer folder ($shown). Extract the whole zip first, or download it again."
    }
    foreach ($name in $missing) { Say "  Note: $name is not in this folder, skipped." 'Yellow' }
    $names = @($names | Where-Object { $missing -notcontains $_ })
    $bytes = 0
    foreach ($name in $names) { $bytes += (Get-Item -LiteralPath (Join-Path $Root ($name -replace '/', '\'))).Length }
    Say ("  {0} files, {1:N1} MB -> {2}" -f $names.Count, ($bytes / 1MB), $target)
    if ($DryRun) { return }

    $stage = $target + '.new'
    $backup = $target + '.old'
    $script:CurrentTarget = $target
    Remove-Folder $stage
    Remove-Folder $backup
    try {
        # 1. Stage: copy, then check every file against the manifest.
        foreach ($name in $names) {
            $source = Join-Path $Root ($name -replace '/', '\')
            $staged = Join-Path $stage ($name -replace '/', '\')
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $staged) | Out-Null
            Copy-Item -LiteralPath $source -Destination $staged -Force
            $expected = $manifest.files.PSObject.Properties[$name].Value.sha256
            if ($expected -and ((Get-Digest $staged $name) -ne $expected)) {
                throw "$name does not match manifest.json (damaged download, or a manifest that was not regenerated). Nothing was installed."
            }
        }
        Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $stage $ManifestName) -Force

        # 2. Core files the new version no longer has: the old manifest of the target tells which ones were ours.
        $gone = @()
        $oldManifest = Join-Path $target $ManifestName
        if (Test-Path -LiteralPath $oldManifest) {
            try {
                $old = Get-Content -Raw -Encoding UTF8 -LiteralPath $oldManifest | ConvertFrom-Json
                $gone = @($old.files.PSObject.Properties.Name | Where-Object { ($null -eq $manifest.files.PSObject.Properties[$_]) -and (Test-Core $_ $packs) })
            } catch { $gone = @() }
        }

        # 3. Swap: each replaced file is moved to the backup folder first; a failure puts everything back.
        $moves = New-Object System.Collections.ArrayList
        try {
            foreach ($name in (@($names) + @($ManifestName))) {
                $relative = $name -replace '/', '\'
                $destination = Join-Path $target $relative
                Move-Aside $destination $backup $moves
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
                Move-Item -LiteralPath (Join-Path $stage $relative) -Destination $destination -Force
            }
            foreach ($name in $gone) {
                Move-Aside (Join-Path $target ($name -replace '/', '\')) $backup $moves
            }
        } catch {
            $reason = $_.Exception.Message
            for ($i = $moves.Count - 1; $i -ge 0; $i--) {
                $destination = $moves[$i][0]
                $saved = $moves[$i][1]
                try {
                    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }
                    if ($saved -and (Test-Path -LiteralPath $saved)) { Move-Item -LiteralPath $saved -Destination $destination -Force }
                } catch { }
            }
            throw "could not replace the files ($reason). The previous version was put back."
        }
    } finally {
        Remove-Folder $stage
    }
    Say '  Done: the core is copied and verified.' 'DarkGreen'
}


function Get-LauncherText($target) {
    # The path sits in a raw string, which cannot end with a backslash: a root such as D:\ is written as r"D:" + "\\".
    $clean = $target.TrimEnd('\')
    $literal = 'r"' + $clean + '"'
    if ($clean.Length -lt $target.Length) { $literal += ' + "\\"' }
    return [IO.File]::ReadAllText($Template).Replace('r"@@AEROTOOLS_ROOT@@"', $literal)
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


function Set-Channel($target) {
    # A copy downloaded from the dev branch keeps following dev for its updates and plugins; without this it would fall back on the stable
    # branch, which has older files. The hint is a small file in the application folder (channel.txt): settings.json is never touched,
    # and a choice saved there (Aero > Updates) always wins. The manifest may name the channel ("channel"); the folder name ending in -dev
    # (what GitHub calls the zip of the dev branch) is the fallback.
    if ($target -eq $Root) { return }
    $name = $null
    try {
        $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $Root $ManifestName) | ConvertFrom-Json
        if ($manifest.PSObject.Properties['channel'] -and $manifest.channel) { $name = [string]$manifest.channel }
    } catch { }
    if (-not $name -and ((Split-Path -Leaf $Root) -match '-dev$')) { $name = 'dev' }
    if (-not $name -or $name -eq 'main') { return }
    $file = Join-Path $target 'channel.txt'
    if (Test-Path -LiteralPath $file) { return }
    Say "  Following the $name branch for updates (this copy comes from it)."
    if ($DryRun) { return }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    [IO.File]::WriteAllText($file, $name + "`n", (New-Object Text.UTF8Encoding($false)))
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
        if (($Root -match '\.zip(\\|$)') -or ($Root -match '\\Temp\d*\\Temp\d+_')) {
            throw "This folder is inside a zip file ($Root). Extract the whole zip first (right-click, Extract All), then run the installer from the extracted folder."
        }
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
    Set-Channel $target
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
