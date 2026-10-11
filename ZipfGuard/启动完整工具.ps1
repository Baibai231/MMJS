$ErrorActionPreference = 'Stop'
$toolRoot = $PSScriptRoot
$pythonPath = Join-Path $toolRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = 'python' }
& $pythonPath (Join-Path $toolRoot 'ops/tool.py') start
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonPath (Join-Path $toolRoot 'ops/open_local_demo.py')
