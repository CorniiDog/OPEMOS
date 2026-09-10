param(
    [ValidateRange(5, 600)]
    [int]$Duration = 45,
    [switch]$NoOpen,
    [switch]$Headless,
    [switch]$Server,
    [string]$PortFile
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-HttpResponse {
    param(
        [System.Net.Sockets.NetworkStream]$Stream,
        [int]$Status,
        [string]$ContentType,
        [byte[]]$Body
    )
    $reason = if ($Status -eq 200) { "OK" } else { "Not Found" }
    $header = "HTTP/1.1 $Status $reason`r`nContent-Type: $ContentType`r`nContent-Length: $($Body.Length)`r`nCache-Control: no-store`r`nContent-Security-Policy: default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`r`nX-Content-Type-Options: nosniff`r`nConnection: close`r`n`r`n"
    $headerBytes = [Text.Encoding]::ASCII.GetBytes($header)
    $Stream.Write($headerBytes, 0, $headerBytes.Length)
    $Stream.Write($Body, 0, $Body.Length)
    $Stream.Flush()
}

function Invoke-LoopbackServer {
    if ([string]::IsNullOrWhiteSpace($PortFile)) {
        throw "The internal server requires a port file."
    }
    $demoPath = Join-Path $PSScriptRoot "interstitial\demo\index.html"
    $pillPath = Join-Path $PSScriptRoot "docs\assets\images\opemos-pill.svg"
    $demo = [IO.File]::ReadAllBytes($demoPath)
    $pill = [IO.File]::ReadAllBytes($pillPath)
    if ($demo.Length -lt 1 -or $demo.Length -gt 1048576) {
        throw "The interstitial demo is empty or excessive."
    }
    if ($pill.Length -lt 1 -or $pill.Length -gt 16384) {
        throw "The OPEMOS pill is empty or excessive."
    }

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
        [IO.File]::WriteAllText($PortFile, "$port`n", [Text.Encoding]::ASCII)
        $deadline = [DateTime]::UtcNow.AddSeconds($Duration)
        while ([DateTime]::UtcNow -lt $deadline) {
            if (-not $listener.Pending()) {
                Start-Sleep -Milliseconds 50
                continue
            }
            $client = $listener.AcceptTcpClient()
            try {
                $client.ReceiveTimeout = 5000
                $client.SendTimeout = 5000
                $stream = $client.GetStream()
                $reader = [IO.StreamReader]::new(
                    $stream,
                    [Text.Encoding]::ASCII,
                    $false,
                    1024,
                    $true
                )
                $requestLine = $reader.ReadLine()
                $headerCount = 0
                while ($headerCount -lt 100) {
                    $line = $reader.ReadLine()
                    if ([string]::IsNullOrEmpty($line)) { break }
                    $headerCount++
                }
                $path = ""
                if ($requestLine -match '^GET ([^ ]+) HTTP/1\.[01]$') {
                    $path = $Matches[1]
                }
                switch ($path) {
                    "/health" {
                        Write-HttpResponse $stream 200 "application/json" ([Text.Encoding]::UTF8.GetBytes('{"schemaVersion":1,"status":"ready"}' + "`n"))
                    }
                    "/" {
                        Write-HttpResponse $stream 200 "text/html; charset=utf-8" $demo
                    }
                    "/index.html" {
                        Write-HttpResponse $stream 200 "text/html; charset=utf-8" $demo
                    }
                    "/opemos-pill.svg" {
                        Write-HttpResponse $stream 200 "image/svg+xml" $pill
                    }
                    default {
                        Write-HttpResponse $stream 404 "text/plain; charset=utf-8" ([Text.Encoding]::UTF8.GetBytes("Not Found`n"))
                    }
                }
            }
            finally {
                $client.Dispose()
            }
        }
    }
    finally {
        $listener.Stop()
    }
}

if ($Server) {
    Invoke-LoopbackServer
    exit 0
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "test_update_windows.ps1 requires Windows."
}

$runtime = Join-Path ([IO.Path]::GetTempPath()) ("opemos-interstitial-windows-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($runtime) | Out-Null
$portPath = Join-Path $runtime "port"
$serverProcess = $null
try {
    $powerShell = (Get-Process -Id $PID).Path
    $quotedScript = '"' + $PSCommandPath + '"'
    $quotedPortFile = '"' + $portPath + '"'
    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        $quotedScript,
        "-Server",
        "-Duration",
        $Duration,
        "-PortFile",
        $quotedPortFile
    )
    $serverProcess = Start-Process -FilePath $powerShell -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $portPath -PathType Leaf)) {
        if ($serverProcess.HasExited) {
            throw "Demo server exited before becoming ready."
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "Demo server did not become ready."
        }
        Start-Sleep -Milliseconds 100
    }
    $portText = [IO.File]::ReadAllText($portPath).Trim()
    $port = 0
    if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "Demo server returned an invalid port."
    }
    $url = "http://127.0.0.1:$port/"
    $health = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri ($url + "health")).Content.Trim()
    if ($health -ne '{"schemaVersion":1,"status":"ready"}') {
        throw "Demo health contract failed."
    }
    $page = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri $url).Content
    foreach ($required in @(
        "CHECKING EXACT NVIDIA SUPPORT",
        "GENERATING INITRAMFS",
        "NVIDIA GRAPHICS READY",
        'id="overall-track"',
        'id="step-track"'
    )) {
        if (-not $page.Contains($required)) {
            throw "Demo page did not contain the required bounded phases."
        }
    }
    $pill = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri ($url + "opemos-pill.svg")).Content
    if (-not $pill.Contains("OPEMOS gradient pill") -or -not $pill.Contains("#76b900")) {
        throw "Demo did not serve the canonical OPEMOS pill."
    }

    if (-not ($NoOpen -or $Headless)) {
        Write-Host "Opening the Linux/SteamOS no-input update interstitial preview for $Duration seconds:"
        Write-Host $url
        Start-Process $url
        if (-not $serverProcess.WaitForExit(($Duration + 10) * 1000)) {
            throw "Demo server exceeded its bounded duration."
        }
    }

    [ordered]@{
        schemaVersion = 1
        status = "passed"
        platform = "windows"
        windowsBrowserSimulation = "passed"
        scope = "Linux/SteamOS interstitial preview; no Windows driver update performed"
    } | ConvertTo-Json -Compress
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        $serverProcess.WaitForExit()
    }
    if (Test-Path -LiteralPath $runtime) {
        Remove-Item -LiteralPath $runtime -Recurse -Force
    }
}
