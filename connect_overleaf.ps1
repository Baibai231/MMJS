$ErrorActionPreference = 'Stop'

$projectUrl = 'https://git@git.overleaf.com/6ac9c5e4a50a568cf0b505a3'
$reportDir = Join-Path $PSScriptRoot 'Overleaf_Report'

if (Test-Path -LiteralPath $reportDir) {
    Write-Host "The report directory already exists: $reportDir"
    Write-Host 'No files were changed. Check the directory before retrying.'
    return
}

Write-Host "Cloning the Overleaf project into: $reportDir"
Write-Host 'Paste your NEW Git authentication token at the next hidden prompt.'
Write-Host 'This script stores it in Windows Credential Manager, not in a project file.'

$secureToken = Read-Host -Prompt 'Overleaf Git token' -AsSecureString
if ($secureToken.Length -eq 0) {
    Write-Host 'No token was entered.'
    return
}

$plainToken = [System.Net.NetworkCredential]::new('', $secureToken).Password
$credentialRecord = "protocol=https`nhost=git.overleaf.com`nusername=git`npassword=$plainToken`n`n"
$credentialRecord | git credential-manager store
$storeExitCode = $LASTEXITCODE
$credentialRecord = $null
$plainToken = $null
$secureToken.Dispose()
if ($storeExitCode -ne 0) {
    Write-Host 'Could not save the token in Windows Credential Manager.'
    return
}

$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'
git clone $projectUrl $reportDir
if ($LASTEXITCODE -ne 0) {
    "protocol=https`nhost=git.overleaf.com`nusername=git`n`n" | git credential-manager erase
    Write-Host 'Clone did not complete. The saved credential was removed.'
    Write-Host 'Check that you used a NEW Overleaf Git authentication token, not your account password.'
    return
}

git -C $reportDir status --short --branch
Write-Host 'Overleaf Git clone is ready.'
