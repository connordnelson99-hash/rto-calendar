#requires -Version 5.1
<#
Wrapper for run_scrapers.py invoked by Windows Task Scheduler.

- Streams stdout/stderr to a dated log file under logs/
- Writes logs/last_run.json with the latest run summary
- Rotates log files older than 30 days
- Shows a Windows toast notification on non-zero exit
#>

$ErrorActionPreference = 'Stop'

$ScriptDir   = $PSScriptRoot
$ProjectRoot = Split-Path $ScriptDir -Parent
$Python      = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Runner      = Join-Path $ScriptDir 'run_scrapers.py'
$LogDir      = Join-Path $ScriptDir 'logs'
$Stamp       = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$LogFile     = Join-Path $LogDir "scrape_$Stamp.log"
$StatusFile  = Join-Path $LogDir 'last_run.json'
$RetainDays  = 30

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# Rotate: delete logs older than $RetainDays days
Get-ChildItem -Path $LogDir -Filter 'scrape_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetainDays) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$startedAt = Get-Date
"=== RTO scraper run started $($startedAt.ToString('s')) ===" | Tee-Object -FilePath $LogFile

# Run scraper. Redirect stderr→stdout so both go to the log.
& $Python $Runner 2>&1 | Tee-Object -FilePath $LogFile -Append
$exitCode = $LASTEXITCODE

$endedAt   = Get-Date
$duration  = [int]($endedAt - $startedAt).TotalSeconds
"=== RTO scraper run ended $($endedAt.ToString('s')) (exit=$exitCode, ${duration}s) ===" |
    Tee-Object -FilePath $LogFile -Append

# Write status summary
$status = [ordered]@{
    started_at   = $startedAt.ToString('o')
    ended_at     = $endedAt.ToString('o')
    duration_sec = $duration
    exit_code    = $exitCode
    log_file     = $LogFile
    success      = ($exitCode -eq 0)
}
$status | ConvertTo-Json | Set-Content -Path $StatusFile -Encoding utf8

# Toast notification on failure (Windows 10/11 built-in, no extra modules)
if ($exitCode -ne 0) {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null

        $template = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>RTO scraper failed</text>
      <text>Exit code $exitCode. See $(Split-Path $LogFile -Leaf)</text>
    </binding>
  </visual>
</toast>
"@
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($template)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('RTO Scraper').Show($toast)
    } catch {
        # Toast is best-effort; never let it mask the real exit code.
        "Toast notification failed: $_" | Add-Content -Path $LogFile
    }
}

exit $exitCode
