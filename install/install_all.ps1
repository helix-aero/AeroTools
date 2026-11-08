# AeroTools: complete installation in a single pass.
#
#   1. adds the Aero menu to Metashape (a small launcher in Metashape's scripts folder)
#   2. looks for a Python 3.10 to 3.13, or installs Python 3.12 with winget
#   3. creates the %USERPROFILE%\AIGCP\_env environment (this is where Aero-GCP looks for it)
#   4. installs the detection engine(s) of your choice:
#        light    (onnxruntime, numpy, opencv: about 100 MB, fast, any PC)  [recommended]
#        precise  (torch + ultralytics: about 3 GB, the original engine, uses the graphics card if present)
#        both
#   5. optionally, the retraining tools (onnx package, plus torch if missing)
#   6. checks that everything imports and that the models\*.pt and models\*.onnx files are there
#
# Without options, the window asks the questions (engine? retraining?). The same choices can be made
# again later from Aero > Aero-GCP > Settings, which runs this installer on the user's behalf.
# Running it again only redoes what is missing. During the long steps the window shows
# "Please wait..." (animated dots), the elapsed time and the last line of the command.
#
# Options: -Engine light|precise|both|none|ask (default ask), -Training (retraining tools),
#          -Yes (asks no question: light engine unless something else is requested),
#          -PauseOnError (waits for a key on failure: for the window launched from Metashape),
#          -NoAI (menu only, no internet), -Check (reports what is missing, installs nothing),
#          -DryRun (shows the commands without running them).
# Messages are ASCII-only: PowerShell 5.1 misreads UTF-8 without a BOM.

param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [ValidateSet('ask', 'light', 'precise', 'both', 'none')][string]$Engine = 'ask',
    [switch]$Training,
    [switch]$Yes,
    [switch]$PauseOnError,
    [switch]$NoAI,
    [switch]$Check,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$EnvDir = Join-Path $env:USERPROFILE 'AIGCP\_env'
$EnvPython = Join-Path $EnvDir 'Scripts\python.exe'
$Models = @('checker', 'spray')
$TorchGpuIndex = 'https://download.pytorch.org/whl/cu128'
$ScriptsDir = Join-Path $env:LOCALAPPDATA 'Agisoft\Metashape Pro\scripts'
$Launcher = Join-Path $ScriptsDir 'aerotools_launcher.py'
$Template = Join-Path $Root 'install\aerotools_launcher.py.tpl'

$script:TotalSteps = 2
$script:StepNo = 0
# When ensurepip does not work on this computer, the environment has no pip: its packages are then installed with the pip
# of the system Python (pip --python <env>). The marker file says this is intentional (otherwise it would look broken every time).
$script:PipViaSystem = $null
$NoPipMarker = Join-Path $EnvDir '.pip_from_system_python'


function Say($text, $color) {
    if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
}


function Show-Step($title) {
    $script:StepNo++
    $done = [int]$script:StepNo - 1
    $bar = ('#' * $done) + '>' + ('.' * [Math]::Max(0, $script:TotalSteps - $done - 1))
    Say ''
    Say ("  [{0}]  Step {1} of {2}: {3}" -f $bar, $script:StepNo, $script:TotalSteps, $title) 'Green'
}


function Quote-Arg($value) {
    if ($value -match '\s') { return '"' + $value + '"' }
    return $value
}


function Read-LastLine($path) {
    # Last non-empty line of a file the command is still writing.
    try {
        $stream = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
        try {
            $reader = New-Object IO.StreamReader($stream)
            $text = $reader.ReadToEnd()
        } finally { $stream.Dispose() }
        $lines = @($text -split "`r?`n" | Where-Object { $_.Trim() })
        if ($lines.Count) { return $lines[-1].Trim() }
    } catch { }
    return ''
}


function Run-Long($label, $exe, [string[]]$arguments) {
    # Runs a long command in the background and animates "Please wait..." while it runs.
    Say ("  > " + $exe + " " + ($arguments -join ' ')) 'DarkGray'
    if ($DryRun) { return }
    $log = Join-Path $env:TEMP ('aerotools_install_{0}.log' -f [guid]::NewGuid().ToString('N').Substring(0, 8))
    $process = Start-Process -FilePath $exe -ArgumentList (($arguments | ForEach-Object { Quote-Arg $_ }) -join ' ') `
        -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError ($log + '.err')
    $null = $process.Handle        # without this PowerShell 5.1 does not keep the exit code
    $frames = @('   ', '.  ', '.. ', '...')
    $tick = 0
    $started = Get-Date
    $width = 100
    try { $width = [Math]::Max(60, $Host.UI.RawUI.WindowSize.Width - 2) } catch { }
    while (-not $process.HasExited) {
        $seconds = [int]((Get-Date) - $started).TotalSeconds
        $last = Read-LastLine $log
        if (-not $last) { $last = Read-LastLine ($log + '.err') }
        $line = '  Please wait{0}  {1:00}:{2:00}  {3}' -f $frames[$tick % 4], [Math]::Floor($seconds / 60), ($seconds % 60), $last
        if ($line.Length -gt $width) { $line = $line.Substring(0, $width) }
        Write-Host ("`r" + $line.PadRight($width)) -NoNewline
        Start-Sleep -Milliseconds 350
        $tick++
    }
    $process.WaitForExit()
    Write-Host ("`r" + (' ' * $width) + "`r") -NoNewline
    if ($process.ExitCode -ne 0) {
        $tail = (Read-LastLine ($log + '.err'))
        if (-not $tail) { $tail = Read-LastLine $log }
        throw ("{0} failed (code {1}). {2}`n  Details: {3}" -f $label, $process.ExitCode, $tail, $log)
    }
    Say ("  Done: {0}" -f $label) 'DarkGreen'
}


function Test-PythonHealthy($path) {
    # A Python whose ensurepip finds its pip package. A half-updated Python (files from two versions) fails here.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # `ensurepip --version` is not enough: it prints the expected version without checking that the matching .whl is there.
        $check = 'import ensurepip, os; v = getattr(ensurepip, ''_PIP_VERSION'', None); d = os.path.join(os.path.dirname(ensurepip.__file__), ''_bundled''); ok = (v is None) or any(n.startswith(''pip-'' + v + ''-'') for n in os.listdir(d)); raise SystemExit(0 if ok else 1)'
        & $path -c $check 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}


function Find-SystemPython {
    # Path of a python.exe 3.10 to 3.13, or $null. A healthy Python is preferred; failing that, the first one found
    # (the installer's fallbacks can then cope with it).
    $candidates = @(@('py', '-3.12'), @('py', '-3.11'), @('py', '-3.13'), @('py', '-3.10'), @('python'))
    $fallback = $null
    foreach ($candidate in $candidates) {
        try {
            $exe = $candidate[0]
            $extra = @($candidate | Select-Object -Skip 1)
            $path = & $exe @extra -c "import sys; print(sys.executable if (3, 10) <= sys.version_info[:2] <= (3, 13) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path -LiteralPath $path)) {
                if (Test-PythonHealthy $path) { return $path }
                if (-not $fallback) { $fallback = $path }
            }
        } catch { }
    }
    return $fallback
}


function Get-PythonProblem($path) {
    # A short sentence if this Python has a problem (incomplete, half-updated, old), otherwise ''.
    if (-not (Test-PythonHealthy $path)) {
        return 'it looks incomplete or half-updated (the file that installs pip does not match its version)'
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $number = & $path -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])' 2>$null
        if ($LASTEXITCODE -eq 0 -and $number -and ([int]$number) -lt 311) { return 'it is an old version (3.10)' }
    } catch { } finally {
        $ErrorActionPreference = $previous
    }
    return ''
}


function Offer-NewPython($path, $reason) {
    # An incomplete or old Python: we offer to install a clean one next to it (leaving the other untouched) and say so clearly.
    # When no question can be asked (-Yes, non-interactive window) or if the user declines, we keep this one: the installer's
    # fallbacks can cope with it in most cases.
    Say ''
    Say ("  The Python found on this computer has a problem: " + $reason + ".") 'Yellow'
    Say ("  ($path)") 'DarkGray'
    $hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
    if (-not (Test-Interactive)) {
        Say '  The installer will try to work around it.' 'Yellow'
        return $path
    }
    if (-not $hasWinget) {
        Say '  Best fix: install Python 3.12 from https://www.python.org/downloads/ (standard installer, tick "Add python.exe to PATH"),' 'Yellow'
        Say '  then run this installer again. Meanwhile it will try to work around the problem.' 'Yellow'
        return $path
    }
    Say '  I can install a clean, up-to-date Python 3.12 next to it (about 100 MB). The other Python is not changed or removed.' 'Cyan'
    $answer = (Read-Host '  Install Python 3.12 now? [Y/n]').Trim().ToLower()
    if ($answer -and $answer -ne 'y' -and $answer -ne 'yes') {
        Say '  OK, the installer will try to work around the problem.' 'Yellow'
        return $path
    }
    try {
        Run-Long 'Python 3.12 installation' 'winget' @('install', '-e', '--id', 'Python.Python.3.12', '--silent', '--accept-package-agreements', '--accept-source-agreements')
    } catch {
        Say ('  Python 3.12 could not be installed: ' + $_.Exception.Message.Split("`n")[0]) 'Yellow'
        return $path
    }
    $candidates = @((Find-SystemPython), (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate) -and -not (Get-PythonProblem $candidate)) { return $candidate }
    }
    Say '  Python 3.12 is installed but this window cannot see it yet: close this window and run the installer again.' 'Yellow'
    return $path
}


function Test-Imports($modules) {
    if (-not (Test-Path -LiteralPath $EnvPython)) { return $false }
    # PowerShell 5.1 turns the stderr of a native program into a fatal error under 'Stop': a missing
    # module (torch, for example) is a normal answer here, not a failure.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $EnvPython -c ("import " + ($modules -join ', ')) 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
}


function Test-EnvPip {
    # True if the environment exists AND pip works in it. An _env folder whose python.exe exists but without pip
    # (interrupted creation, or an environment made by a tool that does not install pip) is not a usable environment.
    if (-not (Test-Path -LiteralPath $EnvPython)) { return $false }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $EnvPython -m pip --version 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}


function Install-PipFromWheel {
    # ensurepip fails if the base Python is inconsistent (partial update: it looks for a .whl version that is not the one
    # in the folder). pip is then installed into the environment from the .whl that is present (no network), whatever
    # its version. Idea taken from a user report where Python 3.13 was half-updated.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $base = & $EnvPython -c 'import sys; print(sys.base_prefix)' 2>$null | Select-Object -First 1
    } catch {
        $base = $null
    } finally {
        $ErrorActionPreference = $previous
    }
    if (-not $base) { return $false }
    $wheel = Get-ChildItem -Path (Join-Path $base 'Lib\ensurepip\_bundled') -Filter 'pip-*.whl' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $wheel) { return $false }
    Say ('  Installing pip from the wheel that comes with Python: ' + $wheel.Name) 'Yellow'
    $savedPath = $env:PYTHONPATH
    $env:PYTHONPATH = $wheel.FullName
    try {
        Run-Long 'pip installation from the bundled wheel' $EnvPython @('-m', 'pip', 'install', '--no-index', '--disable-pip-version-check', $wheel.FullName)
    } catch {
        Say ('  This did not work either: ' + $_.Exception.Message.Split("`n")[0]) 'Yellow'
        return $false
    } finally {
        $env:PYTHONPATH = $savedPath
    }
    return (Test-EnvPip)
}


function Repair-EnvPip {
    # Environment present but without pip: we try ensurepip; otherwise the folder is set aside (nothing is deleted)
    # so that the next step recreates it cleanly.
    Say '  The environment exists but pip is missing: repairing it.' 'Yellow'
    if ($DryRun) { return }
    try {
        Run-Long 'pip repair' $EnvPython @('-m', 'ensurepip', '--upgrade', '--default-pip')
    } catch {
        Say ("  ensurepip did not work: " + $_.Exception.Message.Split("`n")[0]) 'Yellow'
    }
    if (Test-EnvPip) { return }
    if (Install-PipFromWheel) { return }
    $aside = $EnvDir + '_broken_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
    Say "  Setting the broken environment aside: $aside" 'Yellow'
    Move-Item -LiteralPath $EnvDir -Destination $aside -Force
}


function Test-EnvPython {
    # True if the environment's python.exe really starts (a launcher whose base Python has disappeared does not start).
    if (-not (Test-Path -LiteralPath $EnvPython)) { return $false }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $EnvPython -c 'import sys' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}


function Enable-SystemPip($systemPython) {
    # The system Python's pip can install into the environment (pip --python): no pip is then needed inside it.
    if (-not $systemPython -or -not (Test-Path -LiteralPath $systemPython)) { return $false }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $systemPython -m pip --python $EnvPython --disable-pip-version-check list 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
    $script:PipViaSystem = $systemPython
    return $true
}


function Test-EnvReady {
    # Usable environment: python.exe starts and pip works in it, or it was prepared for the system Python's pip.
    if (Test-EnvPip) { return $true }
    return ((Test-Path -LiteralPath $NoPipMarker) -and (Test-EnvPython))
}


function Run-Pip($label, [string[]]$pipArguments) {
    # pip inside the environment; otherwise (no pip inside) the system Python's pip, targeting the environment.
    if ($script:PipViaSystem) {
        Run-Long $label $script:PipViaSystem (@('-m', 'pip', '--python', $EnvPython) + $pipArguments)
    } else {
        Run-Long $label $EnvPython (@('-m', 'pip') + $pipArguments)
    }
}


function Update-Pip {
    # Updating pip is a convenience, not a necessity: a failure here must not stop the installation.
    if ($script:PipViaSystem) { return }
    try {
        Run-Pip 'pip update' @('install', '--upgrade', 'pip', '--progress-bar', 'off', '--disable-pip-version-check')
    } catch {
        Say ("  pip could not be updated (the installation goes on): " + $_.Exception.Message.Split("`n")[0]) 'Yellow'
    }
}


function Test-EngineInstalled { return (Test-Imports @('onnxruntime', 'cv2', 'numpy')) }
function Test-PreciseInstalled { return (Test-Imports @('torch', 'ultralytics')) }
function Test-TrainingInstalled { return (Test-Imports @('torch', 'ultralytics', 'onnx')) }


function Get-LauncherText {
    return [IO.File]::ReadAllText($Template).Replace('@@AEROTOOLS_ROOT@@', $Root)
}


function Test-LauncherCurrent {
    if (-not (Test-Path -LiteralPath $Launcher)) { return $false }
    return ([IO.File]::ReadAllText($Launcher) -ceq (Get-LauncherText))
}


function Install-Launcher {
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'aerotools\bootstrap.py'))) {
        throw "aerotools\bootstrap.py not found in ${Root}: run the installer from the unzipped AeroTools folder."
    }
    if (-not (Test-Path -LiteralPath $Template)) { throw "Template not found: $Template" }
    if (Test-LauncherCurrent) { Say '  The Aero menu is already installed and up to date.'; return }
    Say "  Writing $Launcher"
    if ($DryRun) { return }
    New-Item -ItemType Directory -Force -Path $ScriptsDir | Out-Null
    if (Test-Path -LiteralPath $Launcher) { Copy-Item -LiteralPath $Launcher -Destination ($Launcher + '.bak') -Force }
    [IO.File]::WriteAllText($Launcher, (Get-LauncherText), (New-Object Text.UTF8Encoding($false)))
    Say '  Done: the Aero menu is installed (restart Metashape to see it).' 'DarkGreen'
    foreach ($old in @('active_marker_loader.py', 'metashape_active_marker')) {
        if (Test-Path -LiteralPath (Join-Path $ScriptsDir $old)) {
            Say "  Note: the old script '$old' is still in the Metashape scripts folder. It can stay, but removing it avoids two docks." 'Yellow'
        }
    }
}


function Install-PipFromGetPip {
    # Last resort when ensurepip does not work: get-pip.py (the official pip installer, pypa.io).
    $target = Join-Path $env:TEMP 'aerotools_get-pip.py'
    try {
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
        Say '  Downloading get-pip.py (the official pip installer).'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $target
    } catch {
        Say ('  get-pip.py could not be downloaded: ' + $_.Exception.Message.Split("`n")[0]) 'Yellow'
        return $false
    }
    try {
        Run-Long 'pip installation (get-pip.py)' $EnvPython @($target, '--no-warn-script-location')
    } catch {
        Say ('  get-pip.py did not work: ' + $_.Exception.Message.Split("`n")[0]) 'Yellow'
        return $false
    }
    return (Test-EnvPip)
}


# ---- diagnostic report: install_debug.txt when the installation fails (to send to support)

function Capture($label, $exe, [string[]]$arguments) {
    # Runs a command and returns its text; never raises.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = (& $exe @arguments 2>&1 | ForEach-Object { '' + $_ } | Out-String).Trim()
        $code = $LASTEXITCODE
        $indented = ($out -split "`r?`n" | ForEach-Object { '    ' + $_ }) -join "`r`n"
        return ("  {0}`r`n    exit code {1}`r`n{2}" -f $label, $code, $indented)
    } catch {
        return ("  {0}`r`n    could not run: {1}" -f $label, $_.Exception.Message)
    } finally {
        $ErrorActionPreference = $previous
    }
}


function Tail-File($path, $count) {
    try {
        $lines = @(Get-Content -LiteralPath $path -Tail $count -ErrorAction Stop)
        return (($lines | ForEach-Object { '    ' + $_ }) -join "`r`n")
    } catch { return '    (unreadable)' }
}


function Write-DebugReport($problem) {
    # Writes install_debug.txt (next to the installer, otherwise in TEMP) with enough to understand the failure without having the computer.
    $out = New-Object System.Collections.Generic.List[string]
    $out.Add('AeroTools installer debug report')
    $out.Add(('Date: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')))
    $out.Add(('Problem: ' + $problem))
    try {
        $manifest = Get-Content -Raw -LiteralPath (Join-Path $Root 'manifest.json') | ConvertFrom-Json
        $out.Add(('AeroTools version: {0} (built {1})' -f $manifest.version, $manifest.built))
    } catch { $out.Add('AeroTools version: manifest.json unreadable') }
    $out.Add(('Installer folder: ' + $Root))
    $out.Add('')
    $out.Add('== Computer')
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        $out.Add(('Windows: {0} {1} {2}' -f $os.Caption, $os.Version, $os.OSArchitecture))
    } catch { $out.Add('Windows: unknown') }
    $out.Add(('PowerShell: ' + $PSVersionTable.PSVersion + ' (64-bit process: ' + [Environment]::Is64BitProcess + ')'))
    try {
        $admin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        $out.Add(('Administrator: ' + $admin))
    } catch { }
    $out.Add(('User profile: ' + $env:USERPROFILE + '   (non-ASCII characters: ' + ($env:USERPROFILE -match '[^\x00-\x7F]') + ')'))
    $out.Add(('TEMP: ' + $env:TEMP))
    try {
        $drive = Get-PSDrive -Name ($env:USERPROFILE.Substring(0, 1)) -ErrorAction Stop
        $out.Add(('Free space on the profile drive: {0:N1} GB' -f ($drive.Free / 1GB)))
    } catch { }
    try { $out.Add(('Execution policy: ' + ((Get-ExecutionPolicy -List | ForEach-Object { $_.Scope.ToString() + '=' + $_.ExecutionPolicy }) -join ', '))) } catch { }
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'PIP_INDEX_URL', 'PIP_REQUIRE_VIRTUALENV', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $out.Add(('Environment variable {0} = {1}' -f $name, $value)) }
    }
    try {
        $av = @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName AntiVirusProduct -ErrorAction Stop | ForEach-Object { $_.displayName })
        $out.Add(('Antivirus: ' + ($av -join ', ')))
    } catch { $out.Add('Antivirus: unknown') }
    $out.Add('')
    $out.Add('== Pythons on this computer')
    $out.Add((Capture 'py -0p (Python launcher list)' 'py' @('-0p')))
    $out.Add((Capture 'where python' 'where.exe' @('python')))
    $candidates = New-Object System.Collections.Generic.List[string]
    try { $found = Find-SystemPython; if ($found) { $candidates.Add($found) } } catch { }
    if ($python -and -not $candidates.Contains($python)) { $candidates.Add($python) }
    foreach ($exe in $candidates) {
        $out.Add('')
        $out.Add(('-- ' + $exe))
        $out.Add((Capture 'version and ssl' $exe @('-c', 'import sys, ssl; print(sys.version); print(sys.executable); print(ssl.OPENSSL_VERSION)')))
        $out.Add((Capture 'pip' $exe @('-m', 'pip', '--version')))
        $out.Add((Capture 'ensurepip' $exe @('-m', 'ensurepip', '--version')))
        $out.Add((Capture 'ensurepip bundled wheels' $exe @('-c', 'import ensurepip, os; d = os.path.join(os.path.dirname(ensurepip.__file__), ''_bundled''); print(d); print(os.listdir(d))')))
        $out.Add((Capture 'venv module' $exe @('-c', 'import venv; print(''venv ok'')')))
    }
    $out.Add('')
    $out.Add('== AI environment')
    $out.Add(('Folder: ' + $EnvDir + '   exists: ' + (Test-Path -LiteralPath $EnvDir)))
    if (Test-Path -LiteralPath $EnvDir) {
        try { $out.Add('Content: ' + ((Get-ChildItem -LiteralPath $EnvDir -Force | ForEach-Object { $_.Name }) -join ', ')) } catch { }
        try { $out.Add('Scripts: ' + ((Get-ChildItem -LiteralPath (Join-Path $EnvDir 'Scripts') -Force | ForEach-Object { $_.Name }) -join ', ')) } catch { }
        $cfg = Join-Path $EnvDir 'pyvenv.cfg'
        if (Test-Path -LiteralPath $cfg) { $out.Add('pyvenv.cfg :'); $out.Add((Tail-File $cfg 20)) }
        if (Test-Path -LiteralPath $EnvPython) {
            $out.Add((Capture 'environment python' $EnvPython @('-c', 'import sys; print(sys.version)')))
            $out.Add((Capture 'environment pip' $EnvPython @('-m', 'pip', '--version')))
        }
    }
    $broken = @(Get-ChildItem -LiteralPath (Split-Path -Parent $EnvDir) -Filter '_env_broken_*' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    if ($broken.Count) { $out.Add('Broken environments set aside: ' + ($broken -join ', ')) }
    $out.Add('')
    $out.Add('== Network')
    foreach ($url in @('https://pypi.org/simple/pip/', 'https://bootstrap.pypa.io/get-pip.py')) {
        try {
            try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
            $response = Invoke-WebRequest -UseBasicParsing -Method Head -Uri $url -TimeoutSec 20
            $out.Add(('{0}: HTTP {1}' -f $url, $response.StatusCode))
        } catch { $out.Add(('{0}: {1}' -f $url, $_.Exception.Message.Split("`n")[0])) }
    }
    $out.Add('')
    $out.Add('== Last installer command logs (TEMP)')
    try {
        $logs = @(Get-ChildItem -LiteralPath $env:TEMP -Filter 'aerotools_install_*' -ErrorAction Stop | Sort-Object LastWriteTime -Descending | Select-Object -First 6)
        foreach ($log in $logs) {
            $out.Add(('-- {0} ({1})' -f $log.Name, $log.LastWriteTime.ToString('HH:mm:ss')))
            $out.Add((Tail-File $log.FullName 25))
        }
    } catch { $out.Add('(none)') }
    $text = ($out -join "`r`n")
    foreach ($folder in @($Root, $env:TEMP)) {
        try {
            $path = Join-Path $folder 'install_debug.txt'
            [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($true)))
            Say ''
            Say ('  A debug file was written: ' + $path) 'Yellow'
            Say '  Please send it to the AeroTools team: it tells what is different on this computer.' 'Yellow'
            return
        } catch { }
    }
}


function Exit-With($code) {
    # On failure, keeps the window open when launched from Metashape: the error can be read there.
    if ($PauseOnError -and $code -ne 0) {
        Say ''
        Read-Host '  Press Enter to close this window' | Out-Null
    }
    exit $code
}


function Test-Interactive {
    try { return ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and -not $Yes) } catch { return $false }
}


function Ask-Engine {
    # Returns light, precise or both.
    $tag = @{ light = ''; precise = ''; both = '' }
    if (Test-EngineInstalled) { $tag.light = '  (already installed)' }
    if (Test-PreciseInstalled) { $tag.precise = '  (already installed)' }
    Say ''
    Say '  Which detection engine do you want to install?' 'Cyan'
    Say ('    1  Light    about 100 MB. Fast, works on any computer. Recommended.' + $tag.light)
    Say ('    2  Precise  about 3 GB (PyTorch). The original engine; uses the graphics card if there is one.' + $tag.precise)
    Say '    3  Both     about 3 GB. You choose which one to use in the Aero-GCP settings.'
    while ($true) {
        $answer = (Read-Host '  Your choice [1]').Trim()
        if (-not $answer -or $answer -eq '1') { return 'light' }
        if ($answer -eq '2') { return 'precise' }
        if ($answer -eq '3') { return 'both' }
        Say '  Please type 1, 2 or 3.' 'Yellow'
    }
}


function Ask-Training($willHaveTorch) {
    Say ''
    Say '  Install the retraining tools?' 'Cyan'
    Say '    They let you improve the model with your own corrections (Aero-GCP > Settings > Refine my model).'
    if ($willHaveTorch) {
        Say '    They only add a small package, because the Precise engine tools are already included.'
    } else {
        Say '    They need PyTorch: about 3 GB more.'
    }
    if (Test-TrainingInstalled) { Say '    (already installed)' }
    $answer = (Read-Host '  Install them? [y/N]').Trim().ToLower()
    return ($answer -eq 'y' -or $answer -eq 'yes')
}


function Show-Status {
    $ok = $true
    if (Test-LauncherCurrent) { Say 'Aero menu              : installed' } else { Say 'Aero menu              : NOT installed'; $ok = $false }
    if (-not $NoAI) {
        if (Test-EnvReady) { Say "Python environment    : $EnvPython" }
        elseif (Test-Path -LiteralPath $EnvPython) { Say "Python environment    : BROKEN (pip is missing in $EnvPython): run the installer again to repair it"; $ok = $false }
        else { Say "Python environment    : MISSING ($EnvPython)"; $ok = $false }
        $light = Test-EngineInstalled
        $precise = Test-PreciseInstalled
        $training = Test-TrainingInstalled
        if ($light) { Say 'Light engine          : installed (onnxruntime, numpy, opencv)' }
        elseif ($script:WantLight) { Say 'Light engine          : MISSING'; $ok = $false }
        else { Say 'Light engine          : not installed' }
        if ($precise) { Say 'Precise engine        : installed (torch, ultralytics)' }
        elseif ($script:WantTorch) { Say 'Precise engine        : MISSING'; $ok = $false }
        else { Say 'Precise engine        : not installed' }
        if ($training) { Say 'Retraining tools      : installed' }
        elseif ($Training) { Say 'Retraining tools      : MISSING'; $ok = $false }
        else { Say 'Retraining tools      : not installed (optional)' }
        if (-not $light -and -not $precise) { Say 'No detection engine installed yet.' 'Yellow'; $ok = $false }
        foreach ($name in $Models) {
            foreach ($ext in @('pt', 'onnx')) {
                $path = Join-Path $Root ("models\$name.$ext")
                if (Test-Path -LiteralPath $path) { Say ("Model {0}.{1}: present" -f $name, $ext) } else { Say ("Model {0}.{1}: MISSING ({2})" -f $name, $ext, $path); $ok = $false }
            }
        }
    }
    return $ok
}


# ---- what has to be installed
$script:WantLight = $false
$script:WantTorch = $false
$wantTraining = [bool]$Training

if (-not $NoAI) {
    $choice = $Engine
    if ($choice -eq 'ask') {
        if ($Check -or -not (Test-Interactive)) {
            # When no question can be asked: the light engine, unless only retraining is requested.
            $choice = if ($Training -and -not $Check) { 'none' } elseif ((Test-EngineInstalled) -or (Test-PreciseInstalled)) { 'none' } else { 'light' }
        } else {
            $choice = Ask-Engine
        }
    }
    $script:WantLight = ($choice -eq 'light' -or $choice -eq 'both')
    $script:WantTorch = ($choice -eq 'precise' -or $choice -eq 'both')
    if (-not $Training -and $Engine -eq 'ask' -and -not $Check -and (Test-Interactive)) {
        $wantTraining = Ask-Training ($script:WantTorch)
    }
    if ($wantTraining) { $script:WantTorch = $true }
    $Training = [switch]$wantTraining
}

# Number of steps shown: menu, [python, environment, engines...], final check.
$script:TotalSteps = 2
if (-not $NoAI) {
    $script:TotalSteps += 2
    if ($script:WantLight) { $script:TotalSteps++ }
    if ($script:WantTorch) { $script:TotalSteps++ }
    if ($Training) { $script:TotalSteps++ }
}

Say ''
Say '  ============================================================' 'Green'
Say '    AeroTools - installation' 'Green'
if ($script:WantTorch) {
    Say '    Please wait: this can take 5 to 15 minutes (about 3 GB).' 'Green'
} else {
    Say '    Please wait: this takes a few minutes the first time (about 100 MB).' 'Green'
}
Say '    Do NOT close this window.' 'Green'
Say '  ============================================================' 'Green'

if ($Check) {
    Say ''
    if (Show-Status) { exit 0 } else { exit 2 }
}

try {
    Show-Step 'Adding the Aero menu to Metashape'
    Install-Launcher

    if (-not $NoAI) {
        $python = $null
        $envReady = (Test-EnvPip) -and -not $DryRun
        Show-Step 'Python for the AI detection'
        if ($envReady) {
            Say '  Already installed.'
        } else {
            $python = Find-SystemPython
            if ($python -and -not $DryRun) {
                $problem = Get-PythonProblem $python
                if ($problem) { $python = Offer-NewPython $python $problem }
            }
            if (-not $python) {
                Say '  No Python 3.10 to 3.13 on this computer.'
                if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
                    throw 'winget is missing: install Python 3.12 from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and run this installer again.'
                }
                Run-Long 'Python 3.12 installation' 'winget' @('install', '-e', '--id', 'Python.Python.3.12', '--silent', '--accept-package-agreements', '--accept-source-agreements')
                $python = Find-SystemPython
                if (-not $python) { $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
                if (-not $DryRun -and -not (Test-Path -LiteralPath $python)) {
                    throw 'Python was installed but cannot be found yet: close this window and run the installer again.'
                }
            } else {
                Say "  Using $python"
            }
        }

        Show-Step 'AI environment'
        if (Test-EnvPip) {
            Say '  Already created.'
        } elseif ((Test-Path -LiteralPath $NoPipMarker) -and (Test-EnvPython)) {
            # Set up by a previous installation: no pip in the environment, the system Python's pip is used.
            $systemPython = if ($python) { $python } else { Find-SystemPython }
            if (Enable-SystemPip $systemPython) {
                Say '  Already created (its packages are installed with the pip of the system Python).'
            } else {
                throw "The Python environment has no pip and the system Python cannot install into it. Install Python 3.12 from https://www.python.org/downloads/ (the standard installer, with pip) and run this installer again."
            }
        } else {
            if (Test-Path -LiteralPath $EnvPython) { Repair-EnvPip }
            if (-not (Test-Path -LiteralPath $EnvPython) -and $python) {
                try {
                    Run-Long 'Environment creation' $python @('-m', 'venv', $EnvDir)
                } catch {
                    # Typically: ensurepip fails in the brand-new environment. It is redone without pip, the system pip will do.
                    Say ("  Environment creation with pip failed: " + $_.Exception.Message.Split("`n")[0]) 'Yellow'
                    if (Test-Path -LiteralPath $EnvDir) {
                        # The folder this attempt just created (holding none of the user's work): remove it.
                        Remove-Item -LiteralPath $EnvDir -Recurse -Force -ErrorAction SilentlyContinue
                    }
                    Run-Long 'Environment creation (without pip)' $python @('-m', 'venv', '--without-pip', $EnvDir)
                }
            }
            if (-not $DryRun -and -not (Test-EnvPip) -and (Test-EnvPython)) {
                # Last resort 1: the pip .whl that ships with Python (no network).
                if (Install-PipFromWheel) { Say '  pip was installed in the environment from the wheel that comes with Python.' 'DarkGreen' }
            }
            if (-not $DryRun -and -not (Test-EnvPip) -and (Test-EnvPython)) {
                # Last resort 2: get-pip.py installs pip into the environment.
                if (Install-PipFromGetPip) { Say '  pip was installed in the environment with get-pip.py.' 'DarkGreen' }
            }
            if (-not $DryRun -and -not (Test-EnvPip)) {
                # Last resort 3: the system Python's pip will install into the environment.
                $systemPython = if ($python) { $python } else { Find-SystemPython }
                if ((Test-EnvPython) -and (Enable-SystemPip $systemPython)) {
                    Say '  The environment has no pip on this computer: the pip of the system Python will be used.' 'Yellow'
                    New-Item -ItemType File -Force -Path $NoPipMarker | Out-Null
                } else {
                    throw "The Python environment has no working pip ($EnvPython) and the system Python cannot install into it. Install Python 3.12 from https://www.python.org/downloads/ (the standard installer, with pip) and run this installer again."
                }
            }
        }

        if ($script:WantLight) {
            Show-Step 'Light engine (onnxruntime, numpy, opencv: about 100 MB)'
            if ((Test-EngineInstalled) -and -not $DryRun) {
                Say '  Already installed.'
            } else {
                Update-Pip
                # opencv-python-headless next to an already installed opencv (the one from ultralytics) would make them clash.
                $packages = @('numpy', 'onnxruntime')
                if (-not (Test-Imports @('cv2'))) { $packages += 'opencv-python-headless' }
                Run-Pip 'Light engine installation' (@('install') + $packages + @('--progress-bar', 'off', '--disable-pip-version-check'))
            }
        }

        if ($script:WantTorch) {
            Show-Step 'Precise engine (torch, ultralytics: about 3 GB)'
            if ((Test-PreciseInstalled) -and -not $DryRun) {
                Say '  Already installed.'
            } else {
                Update-Pip
                if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
                    Say '  NVIDIA graphics card found: torch with CUDA.'
                    Run-Pip 'torch installation' @('install', 'torch', 'torchvision', '--index-url', $TorchGpuIndex, '--progress-bar', 'off', '--disable-pip-version-check')
                } else {
                    Say '  No NVIDIA card: torch for the processor.'
                    Run-Pip 'torch installation' @('install', 'torch', 'torchvision', '--progress-bar', 'off', '--disable-pip-version-check')
                }
                Run-Pip 'ultralytics installation' @('install', 'ultralytics', '--progress-bar', 'off', '--disable-pip-version-check')
            }
        }

        if ($Training) {
            Show-Step 'Retraining tools (onnx)'
            if ((Test-TrainingInstalled) -and -not $DryRun) {
                Say '  Already installed.'
            } else {
                Run-Pip 'onnx installation' @('install', 'onnx', '--progress-bar', 'off', '--disable-pip-version-check')
            }
        }
    }

    Show-Step 'Final check'
    if ($DryRun) { Say ''; Say '  Dry run: nothing was installed.'; exit 0 }
    Say ''
    $ready = Show-Status
    Say ''
    if ($ready) {
        Say '  ============================================================' 'Green'
        Say '    All done. Restart Metashape if it is open: Aero menu > Aero-GCP.' 'Green'
        Say '  ============================================================' 'Green'
        exit 0
    }
    Say '  Something is still missing (see above).' 'Yellow'
    # A report only if something we were asked to install is missing (not when no engine was wanted).
    if ($script:WantLight -or $script:WantTorch -or $Training) {
        Write-DebugReport 'Something is still missing after the installation (see the final check above).'
    }
    Exit-With 2
} catch {
    Say ''
    Say ('  ERROR: ' + $_.Exception.Message) 'Red'
    Say '  Nothing is lost: run this installer again, it only redoes what is missing.' 'Yellow'
    try { Write-DebugReport $_.Exception.Message } catch { }
    Exit-With 1
}
