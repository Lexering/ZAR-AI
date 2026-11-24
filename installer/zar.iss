; ZAR - one-click installer (Inno Setup 6). Built by GitHub Actions (.github/workflows/build-installer.yml).
; Keys come in at build time:  ISCC /DGEMINI_KEY=... /DIMAGE_KEY=... /DSTUDENT=... zar.iss
; If GEMINI_KEY is empty the installer asks for it.

#ifndef GEMINI_KEY
  #define GEMINI_KEY ""
#endif
#ifndef IMAGE_KEY
  #define IMAGE_KEY ""
#endif
#ifndef STUDENT
  #define STUDENT ""
#endif
#ifndef APPVER
  #define APPVER "1.0.0"
#endif

[Setup]
AppId={{6B0E2F7A-5A2D-4C3B-9E64-3A7F1C0D2E11}
AppName=ZAR
AppVersion={#APPVER}
AppPublisher=ZAR
AppVerName=ZAR {#APPVER}
DefaultDirName={localappdata}\ZAR
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
#if STUDENT != ""
OutputBaseFilename=ZAR-Setup-{#STUDENT}
#else
OutputBaseFilename=ZAR-Setup
#endif
SetupIconFile=assets\zar.ico
UninstallDisplayIcon={app}\app\installer\zar.ico
WizardStyle=modern
WizardImageFile=assets\wizard_large.bmp
WizardSmallImageFile=assets\wizard_small.bmp
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
es.NameTitle=¿Cómo te llamas?
es.NameDesc=ZAR te llamará por tu nombre.
es.NameLabel=Tu nombre:
es.LangTitle=Idioma
es.LangDesc=¿En qué idioma quieres la interfaz? (ZAR te contestará en el idioma en que le hables)
es.KeyTitle=Clave de IA
es.KeyDesc=ZAR necesita una clave gratuita de Google Gemini.
es.KeyLabel=Entra en aistudio.google.com/apikey, pulsa «Create API key» y pégala aquí:
es.OptLibre=Instalar LibreOffice (documentos y PDF) - recomendado
es.OptLively=Instalar el fondo animado (Lively Wallpaper) - recomendado
es.StPython=Instalando el motor de ZAR...
es.StPackages=Instalando los componentes de IA (puede tardar 5-15 minutos, no cierres esta ventana)...
es.StBrowser=Instalando el navegador automático...
es.StLively=Instalando el fondo animado...
es.StLibre=Instalando LibreOffice...
es.StStart=Arrancando ZAR...
es.StWallpaper=Poniendo el fondo de pantalla...
es.ErrPython=No se pudo instalar el motor de ZAR. Revisa tu conexión a internet y vuelve a ejecutar el instalador.
es.ErrPackages=Algunos componentes no se instalaron. Revisa tu conexión a internet y vuelve a ejecutar el instalador.
es.RunZar=Abrir ZAR ahora
en.NameTitle=What's your name?
en.NameDesc=ZAR will call you by your name.
en.NameLabel=Your name:
en.LangTitle=Language
en.LangDesc=Which language do you want for the interface? (ZAR answers in the language you speak to it)
en.KeyTitle=AI key
en.KeyDesc=ZAR needs a free Google Gemini key.
en.KeyLabel=Go to aistudio.google.com/apikey, click "Create API key" and paste it here:
en.OptLibre=Install LibreOffice (documents and PDF) - recommended
en.OptLively=Install the animated wallpaper (Lively Wallpaper) - recommended
en.StPython=Installing ZAR's engine...
en.StPackages=Installing the AI components (this can take 5-15 minutes, don't close this window)...
en.StBrowser=Installing the automatic browser...
en.StLively=Installing the animated wallpaper...
en.StLibre=Installing LibreOffice...
en.StStart=Starting ZAR...
en.StWallpaper=Setting the wallpaper...
en.ErrPython=ZAR's engine could not be installed. Check your internet connection and run the installer again.
en.ErrPackages=Some components could not be installed. Check your internet connection and run the installer again.
en.RunZar=Open ZAR now

[Files]
Source: "..\*"; DestDir: "{app}\app"; Excludes: "\.git\*,\.github\*,\dist\*,\installer\deps\*,\memory\*.json,\logs\*,\config\*.json,\config\*.txt,__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\config\*.example.json"; DestDir: "{app}\app\config"; Flags: ignoreversion
Source: "assets\zar.ico"; DestDir: "{app}\app\installer"; Flags: ignoreversion
Source: "deps\python-*-amd64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "deps\lively_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist
Source: "deps\lively\*"; DestDir: "{app}\lively"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{userdesktop}\ZAR"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\zar_guardian.pyw"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\installer\zar.ico"
Name: "{userprograms}\ZAR"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\zar_guardian.pyw"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\installer\zar.ico"

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\app\installer\postinstall.ps1"" -Step stop -App ""{app}"""; Flags: runhidden; RunOnceId: "StopZar"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Code]
var
  NamePage: TInputQueryWizardPage;
  LangPage: TInputOptionWizardPage;
  KeyPage: TInputQueryWizardPage;
  OptPage: TInputOptionWizardPage;

function JsonEsc(const S: String): String;
var I: Integer; C: Char;
begin
  Result := '';
  for I := 1 to Length(S) do begin
    C := S[I];
    if C = '"' then Result := Result + '\"'
    else if C = '\' then Result := Result + '\\'
    else if Ord(C) >= 32 then Result := Result + C;
  end;
end;

procedure InitializeWizard;
begin
  NamePage := CreateInputQueryPage(wpWelcome, CustomMessage('NameTitle'), CustomMessage('NameDesc'), '');
  NamePage.Add(CustomMessage('NameLabel'), False);
  NamePage.Values[0] := '{#STUDENT}';

  LangPage := CreateInputOptionPage(NamePage.ID, CustomMessage('LangTitle'), CustomMessage('LangDesc'), '', True, False);
  LangPage.Add('Español');
  LangPage.Add('English');
  if ActiveLanguage = 'en' then LangPage.SelectedValueIndex := 1 else LangPage.SelectedValueIndex := 0;

  KeyPage := CreateInputQueryPage(LangPage.ID, CustomMessage('KeyTitle'), CustomMessage('KeyDesc'), CustomMessage('KeyLabel'));
  KeyPage.Add('Gemini API key:', False);

  OptPage := CreateInputOptionPage(KeyPage.ID, 'ZAR', '', '', False, False);
  OptPage.Add(CustomMessage('OptLively'));
  OptPage.Add(CustomMessage('OptLibre'));
  OptPage.Values[0] := True;
  OptPage.Values[1] := True;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = KeyPage.ID) and ('{#GEMINI_KEY}' <> '');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = NamePage.ID) and (Trim(NamePage.Values[0]) = '') then begin
    MsgBox(CustomMessage('NameLabel'), mbInformation, MB_OK);
    Result := False;
  end;
  if (CurPageID = KeyPage.ID) and (Length(Trim(KeyPage.Values[0])) < 20) then begin
    MsgBox(CustomMessage('KeyLabel'), mbInformation, MB_OK);
    Result := False;
  end;
end;

function Step(const Name, Status: String; Pos: Integer): Integer;
var Rc: Integer;
begin
  WizardForm.StatusLabel.Caption := Status;
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.ProgressGauge.Position := Pos;
  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
       ExpandConstant('{app}\app\installer\postinstall.ps1') + '" -Step ' + Name +
       ' -App "' + ExpandConstant('{app}') + '" -Tmp "' + ExpandConstant('{tmp}') + '"',
       '', SW_HIDE, ewWaitUntilTerminated, Rc);
  Result := Rc;
end;

procedure WriteConfig;
var Key, Lang, Img, Cfg: String;
begin
  Cfg := ExpandConstant('{app}\app\config');
  ForceDirectories(Cfg);
  if LangPage.SelectedValueIndex = 1 then Lang := 'en' else Lang := 'es';
  SaveStringToFile(Cfg + '\zar.json', '{"name": "' + JsonEsc(Trim(NamePage.Values[0])) + '", "lang": "' + Lang + '"}', False);
  Key := '{#GEMINI_KEY}';
  if Key = '' then Key := Trim(KeyPage.Values[0]);
  Img := '{#IMAGE_KEY}';
  if Copy(Img, 1, 3) = 'hf_' then
    SaveStringToFile(Cfg + '\api_keys.json', '{"gemini_api_key": "' + JsonEsc(Key) + '", "huggingface_token": "' + JsonEsc(Img) + '"}', False)
  else
    SaveStringToFile(Cfg + '\api_keys.json', '{"gemini_api_key": "' + JsonEsc(Key) + '", "gemini_image_key": "' + JsonEsc(Img) + '"}', False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Rc: Integer; Lively: String;
begin
  if CurStep <> ssPostInstall then Exit;
  WriteConfig;
  WizardForm.ProgressGauge.Min := 0;
  WizardForm.ProgressGauge.Max := 100;

  if Step('python', CustomMessage('StPython'), 5) <> 0 then begin
    MsgBox(CustomMessage('ErrPython'), mbError, MB_OK);
    Exit;
  end;
  if Step('packages', CustomMessage('StPackages'), 15) <> 0 then
    MsgBox(CustomMessage('ErrPackages'), mbError, MB_OK);
  Step('browser', CustomMessage('StBrowser'), 70);

  if OptPage.Values[0] then begin
    WizardForm.StatusLabel.Caption := CustomMessage('StLively');
    WizardForm.ProgressGauge.Position := 78;
    Lively := ExpandConstant('{tmp}\lively_setup.exe');
    if FileExists(Lively) and not FileExists(ExpandConstant('{commonpf}\Lively Wallpaper\Lively.exe')) then
      ShellExec('', Lively, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOAUTOLAUNCH', '', SW_SHOW, ewWaitUntilTerminated, Rc);
  end;
  if OptPage.Values[1] then
    Step('libreoffice', CustomMessage('StLibre'), 84);

  Step('autostart', CustomMessage('StStart'), 90);
  Step('start', CustomMessage('StStart'), 92);
  if OptPage.Values[0] then
    Step('wallpaper', CustomMessage('StWallpaper'), 97);
  WizardForm.ProgressGauge.Position := 100;
end;
