# AeroTools : installation complete en un seul passage.
#
#   1. ajoute le menu Aero a Metashape (petit lanceur dans le dossier scripts de Metashape)
#   2. cherche un Python 3.10 a 3.13, ou installe Python 3.12 avec winget
#   3. cree l'environnement %USERPROFILE%\AIGCP\_env (c'est la que Aero-GCP le cherche)
#   4. installe le(s) moteur(s) de detection au choix :
#        leger   (onnxruntime, numpy, opencv : environ 100 Mo, rapide, tout PC)  [conseille]
#        precis  (torch + ultralytics : environ 3 Go, le moteur d'origine, carte graphique si presente)
#        les deux
#   5. en option, les outils de reentrainement (package onnx, plus torch s'il manque)
#   6. verifie que tout s'importe et que les modeles models\*.pt et models\*.onnx sont la
#
# Sans option, la fenetre pose les questions (moteur ? reentrainement ?). Les memes choix se refont
# plus tard depuis Aero > Aero-GCP > Parametres, qui lance cet installeur a la place de l'utilisateur.
# Relancer ne refait que ce qui manque. Pendant les longues etapes la fenetre affiche
# "Please wait..." (points animes), le temps ecoule et la derniere ligne de la commande.
#
# Options : -Engine light|precise|both|none|ask (defaut ask), -Training (outils de reentrainement),
#           -Yes (ne pose aucune question : moteur leger si rien d'autre n'est demande),
#           -PauseOnError (attend une touche en cas d'echec : pour la fenetre lancee depuis Metashape),
#           -NoAI (menu seulement, sans internet), -Check (dit ce qui manque, n'installe rien),
#           -DryRun (montre les commandes sans les lancer).
# Messages en ASCII : PowerShell 5.1 lit mal l'UTF-8 sans BOM.

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
$Models = @('damier', 'bombe')
$TorchGpuIndex = 'https://download.pytorch.org/whl/cu128'
$ScriptsDir = Join-Path $env:LOCALAPPDATA 'Agisoft\Metashape Pro\scripts'
$Launcher = Join-Path $ScriptsDir 'aerotools_launcher.py'
$Template = Join-Path $Root 'install\aerotools_launcher.py.tpl'

$script:TotalSteps = 2
$script:StepNo = 0
# Quand ensurepip ne marche pas sur ce poste, l'environnement n'a pas de pip : on installe alors ses paquets avec le pip
# du Python systeme (pip --python <env>). Le fichier repere dit que c'est voulu (sinon on le croirait casse a chaque fois).
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
    Say ("  [{0}]  Step {1} of {2} : {3}" -f $bar, $script:StepNo, $script:TotalSteps, $title) 'Green'
}


function Quote-Arg($value) {
    if ($value -match '\s') { return '"' + $value + '"' }
    return $value
}


function Read-LastLine($path) {
    # Derniere ligne non vide d'un fichier que la commande est encore en train d'ecrire.
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
    # Lance une commande longue en arriere-plan et anime "Please wait..." tant qu'elle tourne.
    Say ("  > " + $exe + " " + ($arguments -join ' ')) 'DarkGray'
    if ($DryRun) { return }
    $log = Join-Path $env:TEMP ('aerotools_install_{0}.log' -f [guid]::NewGuid().ToString('N').Substring(0, 8))
    $process = Start-Process -FilePath $exe -ArgumentList (($arguments | ForEach-Object { Quote-Arg $_ }) -join ' ') `
        -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError ($log + '.err')
    $null = $process.Handle        # sans cela PowerShell 5.1 ne garde pas le code de retour
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
        throw ("{0} failed (code {1}). {2}`n  Details : {3}" -f $label, $process.ExitCode, $tail, $log)
    }
    Say ("  Done : {0}" -f $label) 'DarkGreen'
}


function Test-PythonHealthy($path) {
    # Un Python dont ensurepip retrouve son paquet pip. Un Python mis a jour a moitie (fichiers de deux versions) echoue ici.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # `ensurepip --version` ne suffit pas : il affiche la version attendue sans verifier que le .whl correspondant est la.
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
    # Chemin d'un python.exe 3.10 a 3.13, ou $null. On prefere un Python en bon etat ; a defaut, le premier trouve
    # (les secours de l'installeur savent alors s'en accommoder).
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
    # Une phrase courte si ce Python pose probleme (incomplet, mis a jour a moitie, ancien), sinon ''.
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
    # Un Python incomplet ou ancien : on propose d'en installer un propre a cote (sans toucher a l'autre) et on le dit clairement.
    # Sans question possible (-Yes, fenetre non interactive) ou si l'utilisateur refuse, on garde celui-ci : les secours de
    # l'installeur savent s'en accommoder dans la plupart des cas.
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
    if ($answer -and $answer -ne 'y' -and $answer -ne 'yes' -and $answer -ne 'o' -and $answer -ne 'oui') {
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
    # PowerShell 5.1 transforme le stderr d'un programme natif en erreur fatale sous 'Stop' : un module
    # absent (torch, par exemple) est ici une reponse normale, pas une panne.
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
    # Vrai si l'environnement existe ET que pip y fonctionne. Un dossier _env dont python.exe existe mais sans pip
    # (creation interrompue, ou environnement fait par un outil qui n'installe pas pip) n'est pas un environnement utilisable.
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
    # ensurepip echoue si le Python de base est incoherent (mise a jour partielle : il cherche une version du .whl qui n'est
    # pas celle du dossier). On installe alors pip dans l'environnement depuis le .whl present (sans reseau), quelle que
    # soit sa version. Idee d'un rapport d'un utilisateur dont le Python 3.13 etait a moitie mis a jour.
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
    # Environnement present mais sans pip : on essaie ensurepip ; sinon on met le dossier de cote (rien n'est efface)
    # pour que l'etape suivante le recree proprement.
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
    # Vrai si le python.exe de l'environnement demarre vraiment (un lanceur dont le Python de base a disparu ne demarre pas).
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
    # Le pip du Python systeme peut installer dans l'environnement (pip --python) : il ne faut alors aucun pip dedans.
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
    # Environnement utilisable : python.exe demarre et pip marche dedans, ou bien il a ete prepare pour le pip du Python systeme.
    if (Test-EnvPip) { return $true }
    return ((Test-Path -LiteralPath $NoPipMarker) -and (Test-EnvPython))
}


function Run-Pip($label, [string[]]$pipArguments) {
    # pip dans l'environnement ; sinon (pas de pip dedans) le pip du Python systeme, qui vise l'environnement.
    if ($script:PipViaSystem) {
        Run-Long $label $script:PipViaSystem (@('-m', 'pip', '--python', $EnvPython) + $pipArguments)
    } else {
        Run-Long $label $EnvPython (@('-m', 'pip') + $pipArguments)
    }
}


function Update-Pip {
    # La mise a jour de pip est un confort, pas une necessite : un echec ici ne doit pas arreter l'installation.
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
        throw "aerotools\bootstrap.py not found in $Root : run the installer from the unzipped AeroTools folder."
    }
    if (-not (Test-Path -LiteralPath $Template)) { throw "Template not found : $Template" }
    if (Test-LauncherCurrent) { Say '  The Aero menu is already installed and up to date.'; return }
    Say "  Writing $Launcher"
    if ($DryRun) { return }
    New-Item -ItemType Directory -Force -Path $ScriptsDir | Out-Null
    if (Test-Path -LiteralPath $Launcher) { Copy-Item -LiteralPath $Launcher -Destination ($Launcher + '.bak') -Force }
    [IO.File]::WriteAllText($Launcher, (Get-LauncherText), (New-Object Text.UTF8Encoding($false)))
    Say '  Done : the Aero menu is installed (restart Metashape to see it).' 'DarkGreen'
    foreach ($old in @('active_marker_loader.py', 'metashape_active_marker')) {
        if (Test-Path -LiteralPath (Join-Path $ScriptsDir $old)) {
            Say "  Note : the old script '$old' is still in the Metashape scripts folder. It can stay, but removing it avoids two docks." 'Yellow'
        }
    }
}


function Install-PipFromGetPip {
    # Dernier recours quand ensurepip ne marche pas : get-pip.py (le programme d'installation officiel de pip, pypa.io).
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


# ---- rapport de diagnostic : debug.txt quand l'installation echoue (a envoyer au support)

function Capture($label, $exe, [string[]]$arguments) {
    # Lance une commande et rend son texte ; ne leve jamais.
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
    # Ecrit install_debug.txt (a cote de l'installeur, sinon dans TEMP) avec de quoi comprendre la panne sans avoir le poste.
    $out = New-Object System.Collections.Generic.List[string]
    $out.Add('AeroTools installer debug report')
    $out.Add(('Date : ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')))
    $out.Add(('Problem : ' + $problem))
    try {
        $manifest = Get-Content -Raw -LiteralPath (Join-Path $Root 'manifest.json') | ConvertFrom-Json
        $out.Add(('AeroTools version : {0} (built {1})' -f $manifest.version, $manifest.built))
    } catch { $out.Add('AeroTools version : manifest.json unreadable') }
    $out.Add(('Installer folder : ' + $Root))
    $out.Add('')
    $out.Add('== Computer')
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        $out.Add(('Windows : {0} {1} {2}' -f $os.Caption, $os.Version, $os.OSArchitecture))
    } catch { $out.Add('Windows : unknown') }
    $out.Add(('Language : ' + [Globalization.CultureInfo]::CurrentUICulture.Name))
    $out.Add(('PowerShell : ' + $PSVersionTable.PSVersion + ' (64-bit process: ' + [Environment]::Is64BitProcess + ')'))
    try {
        $admin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        $out.Add(('Administrator : ' + $admin))
    } catch { }
    $out.Add(('User profile : ' + $env:USERPROFILE + '   (non-ASCII characters: ' + ($env:USERPROFILE -match '[^\x00-\x7F]') + ')'))
    $out.Add(('TEMP : ' + $env:TEMP))
    try {
        $drive = Get-PSDrive -Name ($env:USERPROFILE.Substring(0, 1)) -ErrorAction Stop
        $out.Add(('Free space on the profile drive : {0:N1} GB' -f ($drive.Free / 1GB)))
    } catch { }
    try { $out.Add(('Execution policy : ' + ((Get-ExecutionPolicy -List | ForEach-Object { $_.Scope.ToString() + '=' + $_.ExecutionPolicy }) -join ', '))) } catch { }
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'PIP_INDEX_URL', 'PIP_REQUIRE_VIRTUALENV', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $out.Add(('Environment variable {0} = {1}' -f $name, $value)) }
    }
    try {
        $av = @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName AntiVirusProduct -ErrorAction Stop | ForEach-Object { $_.displayName })
        $out.Add(('Antivirus : ' + ($av -join ', ')))
    } catch { $out.Add('Antivirus : unknown') }
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
    $out.Add(('Folder : ' + $EnvDir + '   exists: ' + (Test-Path -LiteralPath $EnvDir)))
    if (Test-Path -LiteralPath $EnvDir) {
        try { $out.Add('Content : ' + ((Get-ChildItem -LiteralPath $EnvDir -Force | ForEach-Object { $_.Name }) -join ', ')) } catch { }
        try { $out.Add('Scripts : ' + ((Get-ChildItem -LiteralPath (Join-Path $EnvDir 'Scripts') -Force | ForEach-Object { $_.Name }) -join ', ')) } catch { }
        $cfg = Join-Path $EnvDir 'pyvenv.cfg'
        if (Test-Path -LiteralPath $cfg) { $out.Add('pyvenv.cfg :'); $out.Add((Tail-File $cfg 20)) }
        if (Test-Path -LiteralPath $EnvPython) {
            $out.Add((Capture 'environment python' $EnvPython @('-c', 'import sys; print(sys.version)')))
            $out.Add((Capture 'environment pip' $EnvPython @('-m', 'pip', '--version')))
        }
    }
    $broken = @(Get-ChildItem -LiteralPath (Split-Path -Parent $EnvDir) -Filter '_env_broken_*' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    if ($broken.Count) { $out.Add('Broken environments set aside : ' + ($broken -join ', ')) }
    $out.Add('')
    $out.Add('== Network')
    foreach ($url in @('https://pypi.org/simple/pip/', 'https://bootstrap.pypa.io/get-pip.py')) {
        try {
            try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
            $response = Invoke-WebRequest -UseBasicParsing -Method Head -Uri $url -TimeoutSec 20
            $out.Add(('{0} : HTTP {1}' -f $url, $response.StatusCode))
        } catch { $out.Add(('{0} : {1}' -f $url, $_.Exception.Message.Split("`n")[0])) }
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
    # En cas d'echec, garde la fenetre ouverte si on la lance depuis Metashape : on y lit l'erreur.
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
    # Renvoie light, precise ou both.
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
    return ($answer -eq 'y' -or $answer -eq 'yes' -or $answer -eq 'o' -or $answer -eq 'oui')
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
                if (Test-Path -LiteralPath $path) { Say ("Model {0}.{1} : present" -f $name, $ext) } else { Say ("Model {0}.{1} : MISSING ({2})" -f $name, $ext, $path); $ok = $false }
            }
        }
    }
    return $ok
}


# ---- ce qu'il faut installer
$script:WantLight = $false
$script:WantTorch = $false
$wantTraining = [bool]$Training

if (-not $NoAI) {
    $choice = $Engine
    if ($choice -eq 'ask') {
        if ($Check -or -not (Test-Interactive)) {
            # Sans question possible : le moteur leger, sauf si seul le reentrainement est demande.
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

# Nombre d'etapes affichees : menu, [python, environnement, moteurs...], controle final.
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
            # Fait lors d'une installation precedente : pas de pip dans l'environnement, on passe par celui du Python systeme.
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
                    # Typiquement : ensurepip echoue dans l'environnement tout neuf. On le refait sans pip, le pip du systeme suffira.
                    Say ("  Environment creation with pip failed: " + $_.Exception.Message.Split("`n")[0]) 'Yellow'
                    if (Test-Path -LiteralPath $EnvDir) {
                        # Le dossier que cette tentative vient de creer (vide de tout travail de l'utilisateur) : on le retire.
                        Remove-Item -LiteralPath $EnvDir -Recurse -Force -ErrorAction SilentlyContinue
                    }
                    Run-Long 'Environment creation (without pip)' $python @('-m', 'venv', '--without-pip', $EnvDir)
                }
            }
            if (-not $DryRun -and -not (Test-EnvPip) -and (Test-EnvPython)) {
                # Dernier recours 1 : le .whl de pip qui accompagne Python (sans reseau).
                if (Install-PipFromWheel) { Say '  pip was installed in the environment from the wheel that comes with Python.' 'DarkGreen' }
            }
            if (-not $DryRun -and -not (Test-EnvPip) -and (Test-EnvPython)) {
                # Dernier recours 2 : get-pip.py installe pip dans l'environnement.
                if (Install-PipFromGetPip) { Say '  pip was installed in the environment with get-pip.py.' 'DarkGreen' }
            }
            if (-not $DryRun -and -not (Test-EnvPip)) {
                # Dernier recours 3 : le pip du Python systeme installera dans l'environnement.
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
                # opencv-python-headless a cote d'un opencv deja la (celui d'ultralytics) les ferait se marcher dessus.
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
    # Un rapport seulement si quelque chose qu'on a demande a installer manque (pas quand aucun moteur n'etait voulu).
    if ($script:WantLight -or $script:WantTorch -or $Training) {
        Write-DebugReport 'Something is still missing after the installation (see the final check above).'
    }
    Exit-With 2
} catch {
    Say ''
    Say ('  ERROR : ' + $_.Exception.Message) 'Red'
    Say '  Nothing is lost: run this installer again, it only redoes what is missing.' 'Yellow'
    try { Write-DebugReport $_.Exception.Message } catch { }
    Exit-With 1
}
