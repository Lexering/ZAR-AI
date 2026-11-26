# ZAR - installer without .exe (works with Windows 11 "Smart App Control", which blocks unsigned setup programs).
# Started by "Instalar ZAR.bat" from the folder made by Crear_instalador.bat. Shows a small progress window, no console.
param([Parameter(Mandatory = $true)][string]$Pkg)        # ...\ZAR-<alumno>\zar  (app files + installer\deps + config)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'      # Invoke-WebRequest is very slow with its progress bar
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, Microsoft.VisualBasic
[System.Windows.Forms.Application]::EnableVisualStyles()

$App    = Join-Path $env:LOCALAPPDATA 'ZAR'
$AppDir = Join-Path $App 'app'
$Deps   = Join-Path $Pkg 'installer\deps'
$Post   = Join-Path $AppDir 'installer\postinstall.ps1'
$Log    = Join-Path $App 'install.log'
New-Item -ItemType Directory -Force -Path $App | Out-Null
function Log($t) { Add-Content -LiteralPath $Log -Value ("{0}  [direct] {1}" -f (Get-Date -Format 'HH:mm:ss'), $t) -Encoding UTF8 }
function Box($t, $icon = 'Information') { [void][System.Windows.Forms.MessageBox]::Show($t, 'ZAR', 'OK', $icon) }

if (-not (Test-Path -LiteralPath (Join-Path $Pkg 'main.py'))) {
    Box "Primero descomprime el ZIP: clic derecho > Extraer todo > Extraer.`nDespués abre la carpeta y haz doble clic en «Instalar ZAR».`n`nUnzip it first (right-click > Extract all), then double-click «Instalar ZAR»." 'Warning'
    exit 1
}

# ---------- settings that came with the package (name, language, keys) ----------
$cfgSrc = Join-Path $Pkg 'config'
$prof = @{ name = ''; lang = 'es' }
try { $j = Get-Content -LiteralPath (Join-Path $cfgSrc 'zar.json') -Raw -Encoding UTF8 | ConvertFrom-Json; $prof.name = "$($j.name)"; if ($j.lang) { $prof.lang = "$($j.lang)" } } catch {}
$keys = [ordered]@{}
try { (Get-Content -LiteralPath (Join-Path $cfgSrc 'api_keys.json') -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $keys[$_.Name] = "$($_.Value)" } } catch {}

# reinstall / update: keep the name, language and keys already on this PC
$oldCfg = Join-Path $env:LOCALAPPDATA 'ZAR\app\config'
try { $j = Get-Content -LiteralPath (Join-Path $oldCfg 'zar.json') -Raw -Encoding UTF8 | ConvertFrom-Json; if (-not $prof.name) { $prof.name = "$($j.name)" }; if ($j.lang) { $prof.lang = "$($j.lang)" } } catch {}
try { (Get-Content -LiteralPath (Join-Path $oldCfg 'api_keys.json') -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties |
      ForEach-Object { if (-not $keys[$_.Name]) { $keys[$_.Name] = "$($_.Value)" } } } catch {}
$es = $prof.lang -ne 'en'
function L($a, $b) { if ($es) { $a } else { $b } }
$name = [Microsoft.VisualBasic.Interaction]::InputBox((L "¿Cómo te llamas? ZAR te llamará por tu nombre." "What's your name? ZAR will call you by it."), 'ZAR', $prof.name)
if ([string]::IsNullOrWhiteSpace($name)) { exit 0 }
if (-not $keys['gemini_api_key'] -or $keys['gemini_api_key'].Length -lt 20) {
    $k = [Microsoft.VisualBasic.Interaction]::InputBox((L "Clave gratuita de Google Gemini (aistudio.google.com/apikey > Create API key):" "Free Google Gemini key (aistudio.google.com/apikey > Create API key):"), 'ZAR', '')
    if ([string]::IsNullOrWhiteSpace($k)) { exit 0 }
    $keys['gemini_api_key'] = $k.Trim()
}

# ---------- progress window ----------
$form = New-Object System.Windows.Forms.Form
$form.Text = (L 'Instalando ZAR' 'Installing ZAR'); $form.Size = New-Object System.Drawing.Size(520, 190)
$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false; $form.MinimizeBox = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(28, 16, 24); $form.ForeColor = [System.Drawing.Color]::FromArgb(251, 239, 231)
$ico = Join-Path $Pkg 'installer\assets\zar.ico'
if (Test-Path -LiteralPath $ico) { try { $form.Icon = New-Object System.Drawing.Icon($ico) } catch {} }
$title = New-Object System.Windows.Forms.Label
$title.Text = 'ZAR'; $title.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 16); $title.AutoSize = $true; $title.Location = '20,14'
$label = New-Object System.Windows.Forms.Label
$label.Font = New-Object System.Drawing.Font('Segoe UI', 10); $label.Size = '470,40'; $label.Location = '22,54'
$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Location = '22,100'; $bar.Size = '460,18'; $bar.Minimum = 0; $bar.Maximum = 100
$form.Controls.AddRange(@($title, $label, $bar))
$form.Show(); $form.Activate()
function Status($t, $p) { $label.Text = $t; $bar.Value = [Math]::Min(100, $p); [System.Windows.Forms.Application]::DoEvents() }
function Wait-Proc($p, [int]$maxSec = 0) {
    $null = $p.Handle; $sw = [Diagnostics.Stopwatch]::StartNew()
    while (-not $p.HasExited) {
        [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 150
        if ($maxSec -gt 0 -and $sw.Elapsed.TotalSeconds -gt $maxSec) { try { $p.Kill() } catch {}; Log "timeout after $maxSec s"; return -1 }
    }
    return $p.ExitCode
}
# Download in small pieces so the window keeps responding, shows the % and gives up if the connection stalls.
function Get-File($url, $dest, $text, $pct) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    for ($try = 1; $try -le 3; $try++) {
        $part = "$dest.part"
        try {
            $req = [Net.HttpWebRequest]::Create($url)
            $req.UserAgent = 'ZAR-installer'; $req.Timeout = 30000; $req.ReadWriteTimeout = 30000; $req.AllowAutoRedirect = $true
            $res = $req.GetResponse(); $total = $res.ContentLength
            $in = $res.GetResponseStream(); $out = [IO.File]::Create($part)
            $buf = New-Object byte[] 262144; $done = 0; $last = 0
            try {
                while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                    $out.Write($buf, 0, $n); $done += $n
                    if ($done - $last -gt 1MB) {
                        $last = $done
                        $mb = [Math]::Round($done / 1MB)
                        Status ($(if ($total -gt 0) { "$text $([Math]::Round(100 * $done / $total)) %" } else { "$text $mb MB" })) $pct
                    }
                }
            } finally { $out.Close(); $in.Close(); $res.Close() }
            if ($total -gt 0 -and $done -lt $total) { throw "incomplete ($done of $total)" }
            Move-Item -LiteralPath $part -Destination $dest -Force
            Log "downloaded $url ($done bytes)"
            return $true
        } catch {
            Log "download $url try $try failed: $_"
            Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
    }
    return $false
}
function Step($name, $text, $pct) {
    Status $text $pct
    $p = Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', "`"$Post`"", '-Step', $name, '-App', "`"$App`"", '-Tmp', "`"$Deps`"")
    $rc = Wait-Proc $p 2700
    Log "step $name -> $rc"
    return $rc
}

try {
    # 1. copy ZAR (keeps memory/config of an earlier install)
    Status (L 'Copiando ZAR...' 'Copying ZAR...') 2
    Step 'stop' '' 2 | Out-Null
    $rc = Wait-Proc (Start-Process robocopy.exe -WindowStyle Hidden -PassThru -ArgumentList @("`"$Pkg`"", "`"$AppDir`"", '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/R:1', '/W:1',
        '/XD', "`"$Deps`"", "`"$(Join-Path $Pkg 'memory')`"", '/XF', 'api_keys.json', 'zar.json', 'API', 'API.txt', 'hud_token.txt'))
    if ($rc -ge 8) { throw "robocopy $rc" }
    Get-ChildItem -LiteralPath $AppDir -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $AppDir 'config'), (Join-Path $AppDir 'memory'), (Join-Path $AppDir 'logs') | Out-Null
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $AppDir 'config\zar.json'), (@{ name = $name.Trim(); lang = $prof.lang } | ConvertTo-Json), $utf8)
    [IO.File]::WriteAllText((Join-Path $AppDir 'config\api_keys.json'), ($keys | ConvertTo-Json), $utf8)
    if (Test-Path -LiteralPath (Join-Path $Deps 'lively')) {
        Wait-Proc (Start-Process robocopy.exe -WindowStyle Hidden -PassThru -ArgumentList @("`"$(Join-Path $Deps 'lively')`"", "`"$(Join-Path $App 'lively')`"", '/E', '/NFL', '/NDL', '/NJH', '/NJS')) | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $App 'lively') -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
    }

    # 2. Python, when installing straight from the GitHub ZIP (no installer\deps folder)
    if (-not (Get-ChildItem -LiteralPath $Deps -Filter 'python-*-amd64.exe' -ErrorAction SilentlyContinue)) {
        $Deps = Join-Path $env:TEMP 'zar-deps'
        New-Item -ItemType Directory -Force -Path $Deps | Out-Null
        $pyf = Join-Path $Deps 'python-3.12.8-amd64.exe'
        if (-not (Test-Path -LiteralPath $pyf) -and -not (Test-Path -LiteralPath (Join-Path $App 'python\python.exe'))) {
            if (-not (Get-File 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' $pyf (L 'Descargando el motor de ZAR...' "Downloading ZAR's engine...") 4)) {
                throw (L 'No se pudo descargar el motor. Revisa internet y vuelve a abrir «Instalar ZAR».' "Couldn't download the engine. Check the internet and open «Instalar ZAR» again.")
            }
        }
    }

    # 3. engine + AI components
    if ((Step 'python' (L 'Instalando el motor de ZAR...' "Installing ZAR's engine...") 6) -ne 0) {
        throw (L 'No se pudo instalar el motor (Python). Revisa internet y vuelve a abrir «Instalar ZAR».' "Python could not be installed. Check the internet and open «Instalar ZAR» again.")
    }
    if ((Step 'packages' (L 'Instalando los componentes de IA (5-15 minutos, no cierres esta ventana)...' 'Installing the AI components (5-15 minutes, keep this window open)...') 15) -ne 0) {
        Box (L 'Algunos componentes no se instalaron. Revisa internet y vuelve a abrir «Instalar ZAR».' 'Some components did not install. Check the internet and open «Instalar ZAR» again.') 'Warning'
    }
    Step 'browser' (L 'Instalando el navegador automático...' 'Installing the automatic browser...') 70 | Out-Null

    # 4. animated wallpaper (Lively): with winget (Windows' own installer), the Microsoft Store as a fallback
    Status (L 'Instalando el fondo animado...' 'Installing the animated wallpaper...') 76
    $haveLively = @("$env:ProgramFiles\Lively Wallpaper\Lively.exe", "${env:ProgramFiles(x86)}\Lively Wallpaper\Lively.exe",
                    "$env:LOCALAPPDATA\Programs\Lively Wallpaper\Lively.exe") | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    function Has-Lively { ($haveLively) -or (Get-AppxPackage -Name '*Lively*' -ErrorAction SilentlyContinue) -or
        (@("$env:ProgramFiles\Lively Wallpaper\Lively.exe", "$env:LOCALAPPDATA\Programs\Lively Wallpaper\Lively.exe") | Where-Object { Test-Path -LiteralPath $_ }) }
    if (-not (Has-Lively)) {
        $ls = Join-Path $Deps 'lively_setup.exe'
        if (Test-Path -LiteralPath $ls) {      # package made by Crear_instalador.bat
            try { Unblock-File -LiteralPath $ls; Wait-Proc (Start-Process $ls -PassThru -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOAUTOLAUNCH') 600 | Out-Null }
            catch { Log "lively setup blocked: $_" }
        }
        if (-not (Has-Lively) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
            foreach ($src in @(@('--id', 'rocksdanister.LivelyWallpaper', '--source', 'winget'), @('--id', '9NTM2QC6QWS7', '--source', 'msstore'))) {
                Status (L 'Instalando el fondo animado (puede tardar unos minutos)...' 'Installing the animated wallpaper (this can take a few minutes)...') 78
                $rc = Wait-Proc (Start-Process winget -WindowStyle Hidden -PassThru -ArgumentList (@('install', '-e') + $src +
                      @('--accept-package-agreements', '--accept-source-agreements', '--silent', '--disable-interactivity'))) 900
                Log "winget lively $($src[1]) -> $rc"
                if (Has-Lively) { break }
            }
        }
    }
    # Lively's command tool (sets ZAR as the wallpaper): small download
    if ((Has-Lively) -and -not (Test-Path -LiteralPath (Join-Path $App 'lively\Livelycu.exe'))) {
        try {
            $rel = Invoke-RestMethod 'https://api.github.com/repos/rocksdanister/lively/releases/latest' -UseBasicParsing -TimeoutSec 20
            $cu = $rel.assets | Where-Object { $_.name -eq 'lively_command_utility.zip' } | Select-Object -First 1
            $z = Join-Path $Deps 'cu.zip'
            if ($cu -and (Get-File $cu.browser_download_url $z (L 'Preparando el fondo animado...' 'Preparing the animated wallpaper...') 80)) {
                Expand-Archive $z -DestinationPath (Join-Path $App 'lively') -Force
                Get-ChildItem -LiteralPath (Join-Path $App 'lively') -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
            }
        } catch { Log "lively tool: $_" }
    }
    if (-not (Has-Lively)) { Log "no Lively: ZAR works, without the animated wallpaper" }
    Step 'libreoffice' (L 'Instalando LibreOffice (documentos y PDF)...' 'Installing LibreOffice (documents and PDF)...') 84 | Out-Null

    # 5. start with Windows, start now, wallpaper, shortcuts
    Step 'autostart' (L 'Arrancando ZAR...' 'Starting ZAR...') 90 | Out-Null
    Step 'start' (L 'Arrancando ZAR...' 'Starting ZAR...') 92 | Out-Null
    if (Has-Lively) { Step 'wallpaper' (L 'Poniendo el fondo de pantalla...' 'Setting the wallpaper...') 96 | Out-Null }
    $sh = New-Object -ComObject WScript.Shell
    $pyw = Join-Path $App 'python\pythonw.exe'
    foreach ($dir in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))) {
        $lnk = $sh.CreateShortcut((Join-Path $dir 'ZAR.lnk'))
        $lnk.TargetPath = $pyw; $lnk.Arguments = "`"$(Join-Path $AppDir 'zar_guardian.pyw')`""; $lnk.WorkingDirectory = $AppDir
        $lnk.IconLocation = (Join-Path $AppDir 'installer\assets\zar.ico'); $lnk.Save()
    }
    # uninstaller next to it
    $un = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"%~dp0app\installer\postinstall.ps1`" -Step stop -App `"%~dp0.`"`r`n" +
          "del `"%USERPROFILE%\Desktop\ZAR.lnk`" `"%APPDATA%\Microsoft\Windows\Start Menu\Programs\ZAR.lnk`" 2>nul`r`nrmdir /s /q `"%~dp0python`" `"%~dp0app`"`r`necho ZAR desinstalado.`r`npause`r`n"
    [IO.File]::WriteAllText((Join-Path $App 'Desinstalar ZAR.bat'), $un, [Text.Encoding]::ASCII)
    Status (L '¡Listo!' 'Done!') 100
    $form.Close()
    Box (L "¡ZAR está instalado, $name!`n`nArranca solo en segundo plano cada vez que enciendes el ordenador, con su fondo animado.`nDi «ZAR» para hablarle." "ZAR is installed, $name!`n`nIt starts by itself in the background every time you turn on the computer, with its animated wallpaper.`nSay «ZAR» to talk to it.")
} catch {
    Log "ERROR $_"
    $form.Close()
    Box ("$_`n`n" + (L "Registro: $Log" "Log: $Log")) 'Error'
    exit 1
}
