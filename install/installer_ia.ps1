# Installation de la detection IA d'AeroTools (FastGCP) : Python + torch + ultralytics.
#
# Ce que fait le script, dans l'ordre (rien n'est installe sans que tu le saches) :
#   1. cherche un Python 3.10 a 3.13 ; s'il n'y en a pas, l'installe avec winget (Python 3.12)
#   2. cree l'environnement %USERPROFILE%\AIGCP\_env (c'est la que FastGCP le cherche)
#   3. installe torch (version carte graphique NVIDIA si une est detectee, sinon processeur) puis ultralytics
#   4. verifie que tout s'importe et que les poids models\damier.pt et models\bombe.pt sont la
#
# Options : -Check (ne fait que dire ce qui manque), -DryRun (montre les commandes sans les lancer).
# Messages en ASCII : PowerShell 5.1 lit mal l'UTF-8 sans BOM.

param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$Check,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$EnvDir = Join-Path $env:USERPROFILE 'AIGCP\_env'
$EnvPython = Join-Path $EnvDir 'Scripts\python.exe'
$Weights = @('damier.pt', 'bombe.pt')
$TorchGpuIndex = 'https://download.pytorch.org/whl/cu128'


function Say($text) { Write-Host $text }


function Find-SystemPython {
    # Renvoie le chemin d'un python.exe 3.10 a 3.13, ou $null.
    $candidates = @(
        @('py', '-3.12'), @('py', '-3.11'), @('py', '-3.13'), @('py', '-3.10'), @('python')
    )
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


function Test-EnvImports {
    if (-not (Test-Path -LiteralPath $EnvPython)) { return $false }
    & $EnvPython -c "import torch, ultralytics" 2>$null
    return ($LASTEXITCODE -eq 0)
}


function Show-Status {
    $ok = $true
    if (Test-Path -LiteralPath $EnvPython) { Say "Environnement Python : $EnvPython" } else { Say "Environnement Python : ABSENT ($EnvPython)"; $ok = $false }
    if (Test-EnvImports) { Say 'torch et ultralytics      : installes' } else { Say 'torch et ultralytics      : ABSENTS'; $ok = $false }
    foreach ($name in $Weights) {
        $path = Join-Path $Root "models\$name"
        if (Test-Path -LiteralPath $path) { Say "Poids $name : presents" } else { Say "Poids $name : ABSENTS ($path)"; $ok = $false }
    }
    return $ok
}


function Run($exe, [string[]]$arguments) {
    Say ("> " + $exe + " " + ($arguments -join ' '))
    if ($DryRun) { return }
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "La commande a echoue (code $LASTEXITCODE)." }
}


Say ''
Say '=== Detection IA d''AeroTools : installation ==='
Say ''

if ($Check) {
    $ready = Show-Status
    if ($ready) { exit 0 } else { exit 2 }
}

if ((Test-EnvImports) -and -not $DryRun) {
    Say 'L''environnement IA est deja installe.'
} else {
    # 1. Python
    $python = Find-SystemPython
    if (-not $python) {
        Say 'Aucun Python 3.10 a 3.13 trouve sur ce poste.'
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) {
            Say 'winget est absent : installe Python 3.12 depuis https://www.python.org/downloads/ (coche "Add python.exe to PATH"),'
            Say 'puis relance cet installeur.'
            exit 1
        }
        Say 'Installation de Python 3.12 avec winget...'
        Run 'winget' @('install', '-e', '--id', 'Python.Python.3.12', '--silent', '--accept-package-agreements', '--accept-source-agreements')
        $python = Find-SystemPython
        if (-not $python) { $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
        if (-not $DryRun -and -not (Test-Path -LiteralPath $python)) {
            Say 'Python vient d''etre installe mais reste introuvable : ferme cette fenetre et relance l''installeur.'
            exit 1
        }
    }
    Say "Python utilise : $python"

    # 2. Environnement
    if (-not (Test-Path -LiteralPath $EnvPython)) {
        Run $python @('-m', 'venv', $EnvDir)
    }

    # 3. torch (carte NVIDIA ou processeur) puis ultralytics
    Run $EnvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
    $nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidia) {
        Say 'Carte graphique NVIDIA detectee : torch avec CUDA (telechargement d''environ 3 Go).'
        Run $EnvPython @('-m', 'pip', 'install', 'torch', 'torchvision', '--index-url', $TorchGpuIndex)
    } else {
        Say 'Pas de carte NVIDIA : torch pour processeur (la detection sera plus lente).'
        Run $EnvPython @('-m', 'pip', 'install', 'torch', 'torchvision')
    }
    Run $EnvPython @('-m', 'pip', 'install', 'ultralytics')
}

Say ''
if ($DryRun) { Say 'Essai a blanc : rien n''a ete installe.'; exit 0 }
$ready = Show-Status
Say ''
if ($ready) {
    Say 'Tout est pret. Relance Metashape : le bouton Run de FastGCP peut detecter.'
    exit 0
}
Say 'Il manque encore quelque chose (voir ci-dessus).'
exit 2
