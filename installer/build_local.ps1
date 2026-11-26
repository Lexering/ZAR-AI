# Builds the installer for one student on THIS Windows PC: dist\ZAR-<student>.zip
# (a folder with «Instalar ZAR.bat»; no .exe, so Windows 11 "Smart App Control" doesn't block it).
# Run it with Crear_instalador.bat (double-click). It asks for the student's name and keys in small windows.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Windows.Forms
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path        # ...\installer
$Root = Split-Path -Parent $Here
$Deps = Join-Path $Here 'deps'
function Ask($text, $title, $def = '') { [Microsoft.VisualBasic.Interaction]::InputBox($text, $title, $def) }
function Box($t, $icon = 'Information') { [void][System.Windows.Forms.MessageBox]::Show($t, 'ZAR - Crear instalador', 'OK', $icon) }
function Say($t) { Write-Host $t -ForegroundColor Cyan }

Say "=========================================="
Say "   ZAR - crear instalador para un alumno"
Say "=========================================="

# 2. Python + Lively (downloaded once, then reused)
New-Item -ItemType Directory -Force -Path $Deps, (Join-Path $Deps 'lively') | Out-Null
$pyVer = '3.12.8'
$pyFile = Join-Path $Deps "python-$pyVer-amd64.exe"
if (-not (Test-Path -LiteralPath $pyFile)) {
    Say "Descargando Python..."
    Invoke-WebRequest "https://www.python.org/ftp/python/$pyVer/python-$pyVer-amd64.exe" -OutFile $pyFile -UseBasicParsing
}
$livelyFile = Join-Path $Deps 'lively_setup.exe'
if (-not (Test-Path -LiteralPath $livelyFile)) {
    Say "Descargando Lively Wallpaper..."
    try {
        $rel = Invoke-RestMethod 'https://api.github.com/repos/rocksdanister/lively/releases/latest' -UseBasicParsing
        $asset = $rel.assets | Where-Object { $_.name -like 'lively_setup_x86_full_*.exe' } | Select-Object -First 1
        if ($asset) { Invoke-WebRequest $asset.browser_download_url -OutFile $livelyFile -UseBasicParsing }
        $cu = $rel.assets | Where-Object { $_.name -eq 'lively_command_utility.zip' } | Select-Object -First 1
        if ($cu) {
            $z = Join-Path $Deps 'cu.zip'
            Invoke-WebRequest $cu.browser_download_url -OutFile $z -UseBasicParsing
            Expand-Archive $z -DestinationPath (Join-Path $Deps 'lively') -Force
            Remove-Item $z
        }
    } catch { Say "No se pudo descargar Lively ($_). El instalador se hará sin el fondo animado." }
}

# 3. Student and keys
$student = Ask "Nombre del alumno (se usa en el nombre del archivo y aparece ya escrito en el instalador):" 'ZAR - Alumno'
if ($null -eq $student) { exit 0 }
$student = ($student -replace '[^\p{L}\p{Nd}_-]', '')
$gem = Ask "Clave de Gemini del alumno (déjalo vacío para que el instalador se la pida):" 'ZAR - Clave de Gemini'
$img = Ask "Clave para imágenes (opcional): una clave de Gemini con facturación o un token de Hugging Face hf_..." 'ZAR - Clave de imágenes'
$ver = (Get-Content -LiteralPath (Join-Path $Root 'version.txt') -Raw).Trim()

# 4. Build the package: dist\ZAR-<student>\ (Instalar ZAR.bat + zar\) and dist\ZAR-<student>.zip
Say "Creando el instalador..."
$dist = Join-Path $Root 'dist'
$name = if ($student) { "ZAR-$student" } else { 'ZAR' }
$out = Join-Path $dist $name
$pk = Join-Path $out 'zar'
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
New-Item -ItemType Directory -Force -Path $pk | Out-Null
& robocopy.exe $Root $pk /E /NFL /NDL /NJH /NJS /R:1 /W:1 /XD (Join-Path $Root '.git') (Join-Path $Root '.github') $dist $Deps (Join-Path $Root 'logs') `
    (Join-Path $Root 'memory\backups') __pycache__ /XF *.pyc api_keys.json zar.json hud_token.txt telegram.json my_voice.pkl API API.txt | Out-Null
Get-ChildItem -LiteralPath (Join-Path $pk 'memory') -Filter *.json -ErrorAction SilentlyContinue | Remove-Item -Force
$pkDeps = Join-Path $pk 'installer\deps'
New-Item -ItemType Directory -Force -Path $pkDeps | Out-Null
Copy-Item -LiteralPath $pyFile -Destination $pkDeps
if (Test-Path -LiteralPath $livelyFile) { Copy-Item -LiteralPath $livelyFile -Destination $pkDeps }
if (Test-Path -LiteralPath (Join-Path $Deps 'lively')) { Copy-Item -LiteralPath (Join-Path $Deps 'lively') -Destination $pkDeps -Recurse }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$k = [ordered]@{ gemini_api_key = "$gem".Trim() }
if ("$img".Trim()) { if ("$img".Trim().StartsWith('hf_')) { $k.huggingface_token = "$img".Trim() } else { $k.gemini_image_key = "$img".Trim() } }
[IO.File]::WriteAllText((Join-Path $pk 'config\api_keys.json'), ($k | ConvertTo-Json), $utf8)
[IO.File]::WriteAllText((Join-Path $pk 'config\zar.json'), (@{ name = $student; lang = 'es' } | ConvertTo-Json), $utf8)
$bat = "@echo off`r`nstart `"`" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"%~dp0zar\installer\install_direct.ps1`" -Pkg `"%~dp0zar`"`r`n"
[IO.File]::WriteAllText((Join-Path $out 'Instalar ZAR.bat'), $bat, [Text.Encoding]::ASCII)
$zip = Join-Path $dist "$name.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $out '*') -DestinationPath $zip -CompressionLevel Optimal
Box "Listo: dist\$name.zip`n`nPara instalar: descomprime el ZIP y haz doble clic en «Instalar ZAR».`nEnvíaselo solo a ese alumno: lleva su clave dentro."
Start-Process explorer.exe $dist
