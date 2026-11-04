# AeroTools : installation complete en un seul passage.
#
#   1. ajoute le menu Aero a Metashape (petit lanceur dans le dossier scripts de Metashape)
#   2. cherche un Python 3.10 a 3.13, ou installe Python 3.12 avec winget
#   3. cree l'environnement %USERPROFILE%\AIGCP\_env (c'est la que FastGCP le cherche)
#   4. installe le moteur de detection : onnxruntime, numpy, opencv (environ 100 Mo, sans torch)
#   5. verifie que tout s'importe et que les modeles models\*.pt et models\*.onnx sont la
#   (option -Training) installe aussi torch, ultralytics et onnx (environ 3 Go) : seulement pour
#   reentrainer le modele ("Refine my model" dans les parametres), jamais pour detecter.
#
# Relancer ne refait que ce qui manque. Pendant les longues etapes la fenetre affiche
# "Please wait..." (points animes), le temps ecoule et la derniere ligne de la commande.
#
# Options : -NoAI (menu seulement, sans internet), -Training, -Check (dit ce qui manque,
#           n'installe rien), -DryRun (montre les commandes sans les lancer).
# Messages en ASCII : PowerShell 5.1 lit mal l'UTF-8 sans BOM.

param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$NoAI,
    [switch]$Training,
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

$TotalSteps = if ($NoAI) { 2 } elseif ($Training) { 6 } else { 5 }
$script:StepNo = 0


function Say($text, $color) {
    if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
}


function Show-Step($title) {
    $script:StepNo++
    $done = [int]$script:StepNo - 1
    $bar = ('#' * $done) + '>' + ('.' * ($TotalSteps - $done - 1))
    Say ''
    Say ("  [{0}]  Step {1} of {2} : {3}" -f $bar, $script:StepNo, $TotalSteps, $title) 'Green'
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


function Test-EngineInstalled { return (Test-Imports @('onnxruntime', 'cv2', 'numpy')) }
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


function Show-Status {
    $ok = $true
    if (Test-LauncherCurrent) { Say 'Aero menu              : installed' } else { Say 'Aero menu              : NOT installed'; $ok = $false }
    if (-not $NoAI) {
        if (Test-Path -LiteralPath $EnvPython) { Say "Python environment    : $EnvPython" } else { Say "Python environment    : MISSING ($EnvPython)"; $ok = $false }
        if (Test-EngineInstalled) { Say 'Detection engine      : installed (onnxruntime, numpy, opencv)' } else { Say 'Detection engine      : MISSING'; $ok = $false }
        foreach ($name in $Models) {
            foreach ($ext in @('pt', 'onnx')) {
                $path = Join-Path $Root ("models\$name.$ext")
                if (Test-Path -LiteralPath $path) { Say ("Model {0}.{1} : present" -f $name, $ext) } else { Say ("Model {0}.{1} : MISSING ({2})" -f $name, $ext, $path); $ok = $false }
            }
        }
        if (Test-TrainingInstalled) {
            Say 'Training tools        : installed'
        } elseif ($Training) {
            Say 'Training tools        : MISSING'; $ok = $false
        } else {
            Say 'Training tools        : not installed (optional, only to retrain the model)'
        }
    }
    return $ok
}


Say ''
Say '  ============================================================' 'Green'
Say '    AeroTools - installation' 'Green'
if ($Training) {
    Say '    Please wait: this can take 5 to 15 minutes (training tools, about 3 GB).' 'Green'
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
        $engineReady = (Test-EngineInstalled) -and -not $DryRun
        $python = $null
        Show-Step 'Python for the AI detection'
        if ($engineReady) {
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
        if (Test-Path -LiteralPath $EnvPython) {
            Say '  Already created.'
        } elseif ($python) {
            Run-Long 'Environment creation' $python @('-m', 'venv', $EnvDir)
        }

        Show-Step 'Detection engine (onnxruntime, numpy, opencv: about 100 MB)'
        if ($engineReady) {
            Say '  Already installed.'
        } else {
            Run-Long 'pip update' $EnvPython @('-m', 'pip', 'install', '--upgrade', 'pip', '--progress-bar', 'off', '--disable-pip-version-check')
            Run-Long 'Detection engine installation' $EnvPython @('-m', 'pip', 'install', 'numpy', 'opencv-python-headless', 'onnxruntime', '--progress-bar', 'off', '--disable-pip-version-check')
        }

        if ($Training) {
            Show-Step 'Training tools (torch, ultralytics, onnx: about 3 GB)'
            if ((Test-TrainingInstalled) -and -not $DryRun) {
                Say '  Already installed.'
            } else {
                if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
                    Say '  NVIDIA graphics card found: torch with CUDA.'
                    Run-Long 'torch installation' $EnvPython @('-m', 'pip', 'install', 'torch', 'torchvision', '--index-url', $TorchGpuIndex, '--progress-bar', 'off', '--disable-pip-version-check')
                } else {
                    Say '  No NVIDIA card: torch for the processor (training will be slow).'
                    Run-Long 'torch installation' $EnvPython @('-m', 'pip', 'install', 'torch', 'torchvision', '--progress-bar', 'off', '--disable-pip-version-check')
                }
                Run-Long 'ultralytics installation' $EnvPython @('-m', 'pip', 'install', 'ultralytics', 'onnx', '--progress-bar', 'off', '--disable-pip-version-check')
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
        Say '    All done. Restart Metashape: Aero menu > FastGCP.' 'Green'
        Say '  ============================================================' 'Green'
        exit 0
    }
    Say '  Something is still missing (see above).' 'Yellow'
    exit 2
} catch {
    Say ''
    Say ('  ERROR : ' + $_.Exception.Message) 'Red'
    Say '  Nothing is lost: run this installer again, it only redoes what is missing.' 'Yellow'
    exit 1
}
