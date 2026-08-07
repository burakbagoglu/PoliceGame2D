$ErrorActionPreference = "Stop"
$toolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $toolDir
$venvDir = Join-Path $repoRoot ".venv"
$python = Join-Path $venvDir "Scripts\python.exe"
$pythonw = Join-Path $venvDir "Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "SD kart araci ilk kez kuruluyor..."
    & py -3 -m venv $venvDir
    & $python -m pip install --upgrade pip
}

& $python -c "import PySide6" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $toolDir "requirements.txt")
}

$appPath = Join-Path $toolDir "app.py"
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    & $pythonw $appPath
} else {
    $appArgument = '"' + $appPath.Replace('"', '\"') + '"'
    Start-Process -Verb RunAs -FilePath $pythonw -WorkingDirectory $repoRoot -ArgumentList $appArgument
}