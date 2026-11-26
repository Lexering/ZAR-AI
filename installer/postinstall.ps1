# ZAR - post-install steps, run by the setup (hidden). One step per call so the setup can show progress.
#   -Step python | packages | browser | libreoffice | autostart | start | wallpaper | stop
param(
    [Parameter(Mandatory = $true)][string]$Step,
    [Parameter(Mandatory = $true)][string]$App,          # install root, e.g. %LOCALAPPDATA%\ZAR
    [string]$Tmp = $env:TEMP
)
$ErrorActionPreference = 'Continue'
$AppDir = Join-Path $App 'app'
$PyDir  = Join-Path $App 'python'
$Py     = Join-Path $PyDir 'python.exe'
$Pyw    = Join-Path $PyDir 'pythonw.exe'
$Log    = Join-Path $App 'install.log'

function Log($t) { Add-Content -LiteralPath $Log -Value ("{0}  [{1}] {2}" -f (Get-Date -Format 'HH:mm:ss'), $Step, $t) -Encoding UTF8 }
function Pip([string[]]$a) {
    & $Py -m pip @a --disable-pip-version-check --no-warn-script-location -q 2>&1 | ForEach-Object { Log $_ }
    return $LASTEXITCODE
}

Log "start"
switch ($Step) {

    'python' {
        if (Test-Path -LiteralPath $Py) { Log "python already there"; break }
        $inst = Get-ChildItem -LiteralPath $Tmp -Filter 'python-*-amd64.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $inst) { Log "python installer missing"; exit 1 }
        $pyArgs = @('/quiet', 'InstallAllUsers=0', "TargetDir=$PyDir", 'Include_launcher=0', 'Include_test=0',
                  'PrependPath=0', 'Shortcuts=0', 'AssociateFiles=0', 'Include_doc=0')
        $p = Start-Process -FilePath $inst.FullName -ArgumentList $pyArgs -Wait -PassThru
        Log "python installer exit $($p.ExitCode)"
        if (-not (Test-Path -LiteralPath $Py)) { exit 1 }
    }

    'packages' {
        Pip @('install', '--upgrade', 'pip', 'wheel') | Out-Null
        # voice recognition: prebuilt webrtcvad (no C++ compiler needed), then resemblyzer without its source build
        Pip @('install', 'webrtcvad-wheels', 'torch', 'scipy', 'librosa') | Out-Null
        Pip @('install', 'resemblyzer', '--no-deps') | Out-Null
        $req = Join-Path $AppDir 'requirements.txt'
        if ((Pip @('install', '-r', $req)) -ne 0) {
            Log "bulk install failed - one by one"
            foreach ($line in Get-Content -LiteralPath $req) {
                $pkg = $line.Trim()
                if (-not $pkg -or $pkg.StartsWith('#')) { continue }
                if ((Pip @('install', $pkg)) -ne 0) { Log "FAILED $pkg" }
            }
        }
        # precompile everything once so every start is faster
        & $Py -m compileall -q -j 0 $AppDir (Join-Path $PyDir 'Lib\site-packages') 2>&1 | Select-Object -Last 3 | ForEach-Object { Log $_ }
        & $Py -c "import numpy, PyQt6, google.genai" 2>&1 | ForEach-Object { Log $_ }
        if ($LASTEXITCODE -ne 0) { Log "core packages missing"; exit 1 }
    }

    'browser' {
        & $Py -m playwright install chromium 2>&1 | ForEach-Object { Log $_ }
    }

    'libreoffice' {
        $have = @("$env:ProgramFiles\LibreOffice\program\soffice.exe", "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe") |
                Where-Object { $_ -and (Test-Path -LiteralPath $_) }
        if ($have) { Log "already installed"; break }
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            & winget install -e --id TheDocumentFoundation.LibreOffice --silent --accept-package-agreements --accept-source-agreements 2>&1 |
                ForEach-Object { Log $_ }
        } else { Log "winget not available - ZAR will install LibreOffice the first time it's needed" }
    }

    'autostart' {
        $cmd = "`"$Pyw`" `"$(Join-Path $AppDir 'zar_guardian.pyw')`""
        New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Force | Out-Null
        Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'ZAR' -Value $cmd
        # Windows waits ~10 s before launching startup apps; remove that wait for this user
        New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize' -Force | Out-Null
        Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize' -Name 'StartupDelayInMSec' -Value 0 -Type DWord
        Log "autostart set (no startup delay)"
    }

    'start' {
        # stop an older copy, then start ZAR hidden through its guardian
        $al = $App.ToLower()
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'python*' -and $_.CommandLine -and $_.CommandLine.ToLower().Contains($al) } |
            ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
        Start-Process -FilePath $Pyw -ArgumentList "`"$(Join-Path $AppDir 'zar_guardian.pyw')`"" -WorkingDirectory $AppDir
        # wait until ZAR's desktop server answers (it puts the token into the wallpaper)
        for ($i = 0; $i -lt 90; $i++) {
            try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5055/state' -TimeoutSec 2 | Out-Null; Log "ZAR is up"; break }
            catch { Start-Sleep -Seconds 2 }
        }
    }

    'wallpaper' {
        $lively = @("$env:ProgramFiles\Lively Wallpaper\Lively.exe", "${env:ProgramFiles(x86)}\Lively Wallpaper\Lively.exe",
                    "$env:LOCALAPPDATA\Programs\Lively Wallpaper\Lively.exe") | Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
                  Select-Object -First 1
        $store = $null
        if (-not $lively) {     # Microsoft Store version (used when Windows blocks Lively's own setup)
            $store = Get-AppxPackage -Name '*Lively*' -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $store) { Log "Lively not found"; exit 1 }
        }
        # Wallpaper input = mouse, so the buttons on the desktop can be clicked
        $setDir = if ($store) { Join-Path $env:LOCALAPPDATA "Packages\$($store.PackageFamilyName)\LocalCache\Local\Lively Wallpaper" } else { Join-Path $env:LOCALAPPDATA 'Lively Wallpaper' }
        $setFile = Join-Path $setDir 'Settings.json'
        try {
            New-Item -ItemType Directory -Force -Path $setDir | Out-Null
            $s = if (Test-Path -LiteralPath $setFile) { Get-Content -LiteralPath $setFile -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
            $s | Add-Member -NotePropertyName 'InputForward' -NotePropertyValue 1 -Force
            $s | Add-Member -NotePropertyName 'Startup' -NotePropertyValue $true -Force
            [IO.File]::WriteAllText($setFile, ($s | ConvertTo-Json -Depth 20))
            Log "Lively settings: mouse input on"
        } catch { Log "could not edit Lively settings: $_" }
        $cu = @((Join-Path $App 'lively\Livelycu.exe'), $(if ($lively) { Join-Path (Split-Path $lively -Parent) 'Livelycu.exe' })) |
              Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
        $wp = Join-Path $AppDir 'wallpaper\index.html'
        if ($store) { Start-Process explorer.exe "shell:AppsFolder\$($store.PackageFamilyName)!App" | Out-Null }
        else { Start-Process -FilePath $lively -ArgumentList 'app', '--showApp', 'false' | Out-Null }
        Start-Sleep -Seconds 10
        $tool = if ($cu) { $cu } else { $lively }
        if (-not $tool) { Log "no Lively command tool"; exit 1 }
        try { Start-Process -FilePath $tool -ArgumentList 'setwp', '--file', "`"$wp`"" -WindowStyle Hidden -Wait; Log "wallpaper set with $tool" }
        catch { Log "could not set the wallpaper: $_"; exit 1 }
        if ($store) { Log "Store Lively starts with Windows by itself"; break }
        # Lively must also start with Windows (so the animated desktop is there after every reboot)
        $run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        $already = (Get-ItemProperty -Path $run -ErrorAction SilentlyContinue).PSObject.Properties |
                   Where-Object { "$($_.Value)" -like '*Lively*' }
        if (-not $already) {
            Set-ItemProperty -Path $run -Name 'LivelyWallpaper' -Value "`"$lively`" app --showApp false"
            Log "Lively autostart set"
        }
    }

    'stop' {
        # used by the uninstaller
        $al = $App.ToLower()
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'python*' -and $_.CommandLine -and $_.CommandLine.ToLower().Contains($al) } |
            ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
        Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'ZAR' -ErrorAction SilentlyContinue
    }
}
Log "done"
exit 0
