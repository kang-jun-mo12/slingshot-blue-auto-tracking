param([switch]$Cpu, [string]$Model, [int]$ImageSize = 1920,
      [ValidateSet('detect','obb')][string]$ExpectedTask, [switch]$RequireBlueTarget)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Run-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program $($Arguments -join ' ')" }
}

$PythonPath = Join-Path $PSScriptRoot '.desktop-venv\Scripts\python.exe'
if (-not (Test-Path $PythonPath)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Run-Checked 'py' @('-3', '-m', 'venv', '.desktop-venv')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        Run-Checked 'python' @('-m', 'venv', '.desktop-venv')
    } else {
        throw 'Install Python 3.12 on this Windows desktop, then run this script again.'
    }
}

$IndexUrl = if ($Cpu) { 'https://download.pytorch.org/whl/cpu' } else { 'https://download.pytorch.org/whl/cu128' }
Run-Checked $PythonPath @('-m', 'pip', 'install', '--upgrade', 'pip')
Run-Checked $PythonPath @('-m', 'pip', 'install', 'torch==2.11.0', 'torchvision==0.26.0', '--index-url', $IndexUrl)
Run-Checked $PythonPath @('-m', 'pip', 'install', '-r', 'requirements-desktop.txt')
$Device = if ($Cpu) { 'cpu' } else { '0' }
Write-Host 'Starting YOLO on port 8090. Keep this window open.'
Write-Host ('Computer: ' + $env:COMPUTERNAME)
if (Get-Command tailscale -ErrorAction SilentlyContinue) { & tailscale ip -4 }
$ServerArguments = @('desktop_inference.py', '--host', '0.0.0.0', '--device', $Device,
                     '--imgsz', "$ImageSize")
if ($Model) { $ServerArguments += @('--model', $Model) }
if ($ExpectedTask) { $ServerArguments += @('--expected-task', $ExpectedTask) }
if ($RequireBlueTarget) { $ServerArguments += '--require-blue-target' }
Run-Checked $PythonPath $ServerArguments
