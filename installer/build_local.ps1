# Builds ZAR-Setup-<student>.exe on THIS Windows PC (same result as the GitHub Action, no GitHub billing needed).
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

# 1. Inno Setup (the tool that makes the .exe)
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    Say "Instalando Inno Setup (solo la primera vez)..."
    & winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements | Out-Host
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $iscc) { Box "No se pudo instalar Inno Setup. Descárgalo de jrsoftware.org/isdl.php, instálalo y vuelve a abrir esto." 'Error'; exit 1 }
}

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

# 4. Build
Say "Creando el instalador..."
Push-Location $Here
& $iscc /Qp "/DGEMINI_KEY=$gem" "/DIMAGE_KEY=$img" "/DSTUDENT=$student" "/DAPPVER=1.0.$ver" 'zar.iss' | Out-Host
$rc = $LASTEXITCODE
Pop-Location
$dist = Join-Path $Root 'dist'
if ($rc -ne 0) { Box "Algo falló al crear el instalador (código $rc). Copia lo que sale en la ventana negra y mándaselo a Claude." 'Error'; exit 1 }
Box "Listo: dist\ZAR-Setup-$student.exe`n`nEnvíaselo solo a ese alumno: lleva su clave dentro."
Start-Process explorer.exe $dist
