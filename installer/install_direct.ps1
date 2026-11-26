# ZAR - installer without .exe (works with Windows 11 "Smart App Control", which blocks unsigned setup programs).
# Started by "Instalar ZAR.bat" from the folder made by Crear_instalador.bat. Shows a small progress window, no console.
param([Parameter(Mandatory = $true)][string]$Pkg)        # ...\ZAR-<alumno>\zar  (app files + installer\deps + config)
$ErrorActionPreference = 'Continue'
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
function Wait-Proc($p) { $null = $p.Handle; while (-not $p.HasExited) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 150 } ; return $p.ExitCode }
function Step($name, $text, $pct) {
    Status $text $pct
    $p = Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', "`"$Post`"", '-Step', $name, '-App', "`"$App`"", '-Tmp', "`"$Deps`"")
    $rc = Wait-Proc $p
    Log "step $name -> $rc"
    return $rc
}

try {
    # 1. copy ZAR (keeps memory/config of an earlier install)
    Status (L 'Copiando ZAR...' 'Copying ZAR...') 2
    Step 'stop' '' 2 | Out-Null
    $rc = Wait-Proc (Start-Process robocopy.exe -WindowStyle Hidden -PassThru -ArgumentList @("`"$Pkg`"", "`"$AppDir`"", '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/R:1', '/W:1',
        '/XD', "`"$Deps`"", "`"$(Join-Path $Pkg 'memory')`"", '/XF', 'api_keys.json', 'zar.json'))
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

    # 2. engine + AI components
    if ((Step 'python' (L 'Instalando el motor de ZAR...' "Installing ZAR's engine...") 6) -ne 0) {
        throw (L 'No se pudo instalar el motor (Python). Revisa internet y vuelve a abrir «Instalar ZAR».' "Python could not be installed. Check the internet and open «Instalar ZAR» again.")
    }
    if ((Step 'packages' (L 'Instalando los componentes de IA (5-15 minutos, no cierres esta ventana)...' 'Installing the AI components (5-15 minutes, keep this window open)...') 15) -ne 0) {
        Box (L 'Algunos componentes no se instalaron. Revisa internet y vuelve a abrir «Instalar ZAR».' 'Some components did not install. Check the internet and open «Instalar ZAR» again.') 'Warning'
    }
    Step 'browser' (L 'Instalando el navegador automático...' 'Installing the automatic browser...') 70 | Out-Null

    # 3. animated wallpaper (Lively): its own setup, or the Microsoft Store version if Windows blocks the setup
    Status (L 'Instalando el fondo animado...' 'Installing the animated wallpaper...') 78
    $haveLively = @("$env:ProgramFiles\Lively Wallpaper\Lively.exe", "${env:ProgramFiles(x86)}\Lively Wallpaper\Lively.exe",
                    "$env:LOCALAPPDATA\Programs\Lively Wallpaper\Lively.exe") | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if (-not $haveLively -and -not (Get-AppxPackage -Name '*Lively*' -ErrorAction SilentlyContinue)) {
        $ok = $false
        $ls = Join-Path $Deps 'lively_setup.exe'
        if (Test-Path -LiteralPath $ls) {
            try { Unblock-File -LiteralPath $ls; $ok = (Wait-Proc (Start-Process $ls -PassThru -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOAUTOLAUNCH')) -eq 0 }
            catch { Log "lively setup blocked: $_" }
        }
        if (-not $ok -and (Get-Command winget -ErrorAction SilentlyContinue)) {
            Log "Lively from the Microsoft Store"
            Wait-Proc (Start-Process winget -WindowStyle Hidden -PassThru -ArgumentList 'install', '--id', '9NTM2QC6QWS7', '--source', 'msstore',
                '--accept-package-agreements', '--accept-source-agreements', '--silent') | Out-Null
        }
    }
    Step 'libreoffice' (L 'Instalando LibreOffice (documentos y PDF)...' 'Installing LibreOffice (documents and PDF)...') 84 | Out-Null

    # 4. start with Windows, start now, wallpaper, shortcuts
    Step 'autostart' (L 'Arrancando ZAR...' 'Starting ZAR...') 90 | Out-Null
    Step 'start' (L 'Arrancando ZAR...' 'Starting ZAR...') 92 | Out-Null
    Step 'wallpaper' (L 'Poniendo el fondo de pantalla...' 'Setting the wallpaper...') 96 | Out-Null
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
