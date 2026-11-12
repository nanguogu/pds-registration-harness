$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridgeUrl = "http://127.0.0.1:8766"
$python = (Get-Command python -ErrorAction Stop).Source

function Test-Bridge {
    try {
        $result = Invoke-RestMethod -Uri "$bridgeUrl/health" -TimeoutSec 1
        return $result.status -eq "ok" -and $result.service -eq "pds-session-bridge"
    }
    catch {
        return $false
    }
}

if (-not (Test-Bridge)) {
    Start-Process `
        -FilePath $python `
        -ArgumentList @(
            "-m", "uvicorn",
            "--app-dir", "src",
            "pds_harness.bridge_app:app",
            "--host", "127.0.0.1",
            "--port", "8766"
        ) `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden | Out-Null

    $ready = $false
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        Start-Sleep -Milliseconds 200
        if (Test-Bridge) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        throw "PDS Session Bridge failed to start on $bridgeUrl"
    }
}

$env:PDS_SESSION_BRIDGE_URL = $bridgeUrl
& $python -m uvicorn --app-dir src pds_harness.webapp:app --host 127.0.0.1 --port 8765

