# AeroTools : installation complete en un seul passage.
#
#   1. ajoute le menu Aero a Metashape (petit lanceur dans le dossier scripts de Metashape)
#   2. cherche un Python 3.10 a 3.13, ou installe Python 3.12 avec winget
#   3. cree l'environnement %USERPROFILE%\AIGCP\_env (c'est la que FastGCP le cherche)
#   4. installe le(s) moteur(s) de detection au choix :
#        leger   (onnxruntime, numpy, opencv : environ 100 Mo, rapide, tout PC)  [conseille]
#        precis  (torch + ultralytics : environ 3 Go, le moteur d'origine, carte graphique si presente)
#        les deux
#   5. en option, les outils de reentrainement (package onnx, plus torch s'il manque)
#   6. verifie que tout s'importe et que les modeles models\*.pt et models\*.onnx sont la
#
# Sans option, la fenetre pose les questions (moteur ? reentrainement ?). Les memes choix se refont
# plus tard depuis Aero > FastGCP > Parametres, qui lance cet installeur a la place de l'utilisateur.
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


function Find-SystemPython {
    # Chemin d'un python.exe 3.10 a 3.13, ou $null.
    $candidates = @(@('py', '-3.12'), @('py', '-3.11'), @('py', '-3.13'), @('py', '-3.10'), @('python'))
    foreach ($candidate in $candidates) {
        try {
            $exe = $candidate[0]
            $extra = @($candidate | Select-Object -Skip 1)
            $path = & $exe @extra -c "import sys; print(sys.executable if (3, 10) <= sys.version_info[:2] <= (3, 13) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path -LiteralPath $path)) { return $path }
        } catch { }
    }
    return $null
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
    $aside = $EnvDir + '_broken_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
    Say "  Setting the broken environment aside: $aside" 'Yellow'
    Move-Item -LiteralPath $EnvDir -Destination $aside -Force
}


function Update-Pip {
    # La mise a jour de pip est un confort, pas une necessite : un echec ici ne doit pas arreter l'installation.
    try {
        Run-Long 'pip update' $EnvPython @('-m', 'pip', 'install', '--upgrade', 'pip', '--progress-bar', 'off', '--disable-pip-version-check')
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
    Say '    3  Both     about 3 GB. You choose which one to use in the FastGCP settings.'
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
    Say '    They let you improve the model with your own corrections (FastGCP > Settings > Refine my model).'
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
        if (Test-EnvPip) { Say "Python environment    : $EnvPython" }
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
        } else {
            if (Test-Path -LiteralPath $EnvPython) { Repair-EnvPip }
            if (-not (Test-Path -LiteralPath $EnvPython) -and $python) {
                Run-Long 'Environment creation' $python @('-m', 'venv', $EnvDir)
            }
            if (-not $DryRun -and -not (Test-EnvPip)) {
                throw "The Python environment has no working pip ($EnvPython). Install Python 3.12 from https://www.python.org/downloads/ (the standard installer, with pip) and run this installer again."
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
                Run-Long 'Light engine installation' $EnvPython (@('-m', 'pip', 'install') + $packages + @('--progress-bar', 'off', '--disable-pip-version-check'))
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
                    Run-Long 'torch installation' $EnvPython @('-m', 'pip', 'install', 'torch', 'torchvision', '--index-url', $TorchGpuIndex, '--progress-bar', 'off', '--disable-pip-version-check')
                } else {
                    Say '  No NVIDIA card: torch for the processor.'
                    Run-Long 'torch installation' $EnvPython @('-m', 'pip', 'install', 'torch', 'torchvision', '--progress-bar', 'off', '--disable-pip-version-check')
                }
                Run-Long 'ultralytics installation' $EnvPython @('-m', 'pip', 'install', 'ultralytics', '--progress-bar', 'off', '--disable-pip-version-check')
            }
        }

        if ($Training) {
            Show-Step 'Retraining tools (onnx)'
            if ((Test-TrainingInstalled) -and -not $DryRun) {
                Say '  Already installed.'
            } else {
                Run-Long 'onnx installation' $EnvPython @('-m', 'pip', 'install', 'onnx', '--progress-bar', 'off', '--disable-pip-version-check')
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
        Say '    All done. Restart Metashape if it is open: Aero menu > FastGCP.' 'Green'
        Say '  ============================================================' 'Green'
        exit 0
    }
    Say '  Something is still missing (see above).' 'Yellow'
    Exit-With 2
} catch {
    Say ''
    Say ('  ERROR : ' + $_.Exception.Message) 'Red'
    Say '  Nothing is lost: run this installer again, it only redoes what is missing.' 'Yellow'
    Exit-With 1
}
