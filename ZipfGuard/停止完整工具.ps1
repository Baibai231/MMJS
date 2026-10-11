$toolRoot = $PSScriptRoot
$pythonPath = Join-Path $toolRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = 'python' }
& $pythonPath (Join-Path $toolRoot 'ops/tool.py') stop
