$ErrorActionPreference = 'Stop'
$toolRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $toolRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = 'python' }
$entryPath = Join-Path $PSScriptRoot 'open_ready_demo.py'
$runtimePath = Join-Path $toolRoot 'tool_runtime'
$outputPath = Join-Path $runtimePath 'ready-demo-launch.log'
$errorPath = Join-Path $runtimePath 'ready-demo-launch.error.log'
Start-Process -FilePath $pythonPath -ArgumentList @('"' + $entryPath + '"') -WorkingDirectory $toolRoot -WindowStyle Hidden -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath
