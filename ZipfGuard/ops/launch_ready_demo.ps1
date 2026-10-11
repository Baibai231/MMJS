$ErrorActionPreference = 'Stop'
$toolRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $toolRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = 'python' }
$entryPath = Join-Path $PSScriptRoot 'open_ready_demo.py'
$runtimePath = Join-Path $toolRoot 'tool_runtime'
$demoPath = Join-Path $runtimePath 'ready-demo'
$statusPath = Join-Path $demoPath 'status.json'
$profilePath = [IO.Path]::GetFullPath((Join-Path $demoPath 'browser'))
New-Item -ItemType Directory -Path $demoPath -Force | Out-Null
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class DemoWindow {
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
}
'@
function Show-DemoBrowser {
    $browserProcesses = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" | Where-Object {
        $command = ([string]$_.CommandLine).Replace('/', '\')
        $command -match '--user-data-dir=' -and $command -notmatch '--type=' -and $command.IndexOf($profilePath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    }
    foreach ($browserProcess in $browserProcesses) {
        $displayProcess = Get-Process -Id $browserProcess.ProcessId -ErrorAction SilentlyContinue
        if ($displayProcess -and $displayProcess.MainWindowHandle -ne 0) {
            $handle = $displayProcess.MainWindowHandle
            [DemoWindow]::ShowWindowAsync($handle, 9) | Out-Null
            [DemoWindow]::SetWindowPos($handle, [IntPtr](-1), 0, 0, 0, 0, 3) | Out-Null
            [DemoWindow]::SetWindowPos($handle, [IntPtr](-2), 0, 0, 0, 0, 3) | Out-Null
            [DemoWindow]::SetForegroundWindow($handle) | Out-Null
            return $true
        }
    }
    return $false
}
if (Show-DemoBrowser) {
    Set-Content -LiteralPath (Join-Path $demoPath 'reopen.request') -Value 'open' -Encoding UTF8
    exit
}
$form = New-Object Windows.Forms.Form
$form.Text = 'TriGuard 展示'
$form.Size = New-Object Drawing.Size(460, 170)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true
$label = New-Object Windows.Forms.Label
$label.Location = New-Object Drawing.Point(24, 20)
$label.Size = New-Object Drawing.Size(405, 60)
$label.Font = New-Object Drawing.Font('Microsoft YaHei', 11)
$label.Text = "正在打开可操作展示…首次启动约需一分钟。请稍候。"
$form.Controls.Add($label)
$bar = New-Object Windows.Forms.ProgressBar
$bar.Location = New-Object Drawing.Point(24, 88)
$bar.Size = New-Object Drawing.Size(405, 18)
$bar.Style = 'Marquee'
$form.Controls.Add($bar)
$form.Show()
[Windows.Forms.Application]::DoEvents()
try {
    $launchTime = [DateTimeOffset]::UtcNow
    $logSuffix = [Guid]::NewGuid().ToString('N').Substring(0, 10)
    $outputPath = Join-Path $runtimePath ('ready-demo-launch-' + $logSuffix + '.log')
    $errorPath = Join-Path $runtimePath ('ready-demo-launch-' + $logSuffix + '.error.log')
    Start-Process -FilePath $pythonPath -ArgumentList @('"' + $entryPath + '"') -WorkingDirectory $toolRoot -WindowStyle Hidden -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath | Out-Null
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    while ([DateTime]::UtcNow -lt $deadline -and -not $form.IsDisposed) {
        [Windows.Forms.Application]::DoEvents()
        if (Test-Path -LiteralPath $statusPath) {
            try { $currentStatus = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $currentStatus = $null }
            if ($currentStatus -and ([DateTimeOffset]::Parse($currentStatus.updated)) -ge $launchTime) {
                if ($currentStatus.status -eq 'error') { throw $currentStatus.message }
                if ($currentStatus.status -eq 'opening_browser') { $label.Text = '服务已启动，正在打开浏览器并登录展示账户…' }
                if ($currentStatus.status -eq 'ready') {
                    $form.Close()
                    Show-DemoBrowser | Out-Null
                    exit
                }
            }
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $form.IsDisposed) { throw ('启动仍未完成。运行记录：' + $errorPath) }
} catch {
    if (-not $form.IsDisposed) { $form.Close() }
    [Windows.Forms.MessageBox]::Show(('无法打开展示：' + $_.Exception.Message), 'TriGuard 展示', 'OK', 'Error') | Out-Null
    exit 1
} finally {
    if (-not $form.IsDisposed) { $form.Dispose() }
}
