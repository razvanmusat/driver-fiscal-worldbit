; Setup.exe pentru agentul "driver fiscal worldbit" (Windows). Se compilează cu Inno Setup 6: ISCC.exe Setup.iss  ->  Output\Setup-DriverFiscalWorldbit.exe
; Driverele casei și ale POS-ului NU se descarcă: wizardul arată linkul (din drivere.json, aceleași valori ca mai jos), omul le instalează și bifează „Am instalat driverul”.
; Instalarea propriu-zisă o face installer\install.ps1 (Python, fișierele, config, task de pornire la boot, politica Chrome).
#define AppName "Driver fiscal Worldbit"

[Setup]
AppName={#AppName}
AppVersion=1.0.1
AppPublisher=Worldbit
CreateAppDir=no
Uninstallable=no
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=yes
PrivilegesRequired=admin
OutputDir=Output
OutputBaseFilename=Setup-DriverFiscalWorldbit
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Messages]
ButtonNext=&Înainte >
ButtonBack=< &Înapoi
ButtonCancel=Renunță
ButtonInstall=&Instalează
ButtonFinish=&Gata
SetupAppTitle=Instalare
SetupWindowTitle=Instalare - %1
WelcomeLabel1=Instalare [name]
WelcomeLabel2=Se instalează agentul local care leagă aplicația de casa de marcat și de POS.%n%nUrmează: alegerea casei de marcat, a POS-ului și a adresei aplicației.
FinishedHeadingLabel=Instalare terminată
FinishedLabelNoIcons=Agentul rulează și pornește singur la fiecare pornire a calculatorului.
FinishedLabel=Agentul rulează și pornește singur la fiecare pornire a calculatorului.
ClickNext=Apasă Înainte pentru a continua sau Renunță pentru a ieși din instalare.
ClickFinish=Apasă Gata pentru a închide instalarea.
WizardInstalling=Se instalează
InstallingLabel=Așteaptă să se instaleze agentul (poate dura câteva minute dacă trebuie instalat și Python).
StatusExtractFiles=Se copiază fișierele...
BeveledLabel=
ExitSetupTitle=Închide instalarea
ExitSetupMessage=Instalarea nu e terminată. Dacă închizi acum, agentul nu se instalează.%n%nÎnchizi instalarea?

[Files]
Source: "..\worldbit_fiscal\*"; DestDir: "{tmp}\worldbit_fiscal"; Flags: recursesubdirs ignoreversion deleteafterinstall; Excludes: "__pycache__"
Source: "install.ps1"; DestDir: "{tmp}\installer"; Flags: ignoreversion deleteafterinstall
Source: "drivere.json"; DestDir: "{tmp}\installer"; Flags: ignoreversion deleteafterinstall

[Run]
Filename: "http://127.0.0.1:{code:Port}/setari"; Description: "Deschide pagina de setări (portul COM al POS-ului, dispozitivul FisCool)"; Flags: postinstall shellexec skipifsilent

[Code]
function SetEnvironmentVariable(lpName, lpValue: String): Boolean; external 'SetEnvironmentVariableW@kernel32.dll stdcall';

const
  LINK_FISCOOL = 'https://fiscool.ro/';
  LINK_FISCO = 'https://fisco.ro/manual-fisco/';
  LINK_NEWLAND = 'https://aimsolutionserv.ro/pkmervRTOL/newland_USB_Driver.rar';

var
  CasaPage, PosPage, AppPage: TWizardPage;
  CasaFiscool, CasaFisco, PosNone, PosNewland: TNewRadioButton;
  CasaLink, CasaNota, PosLink, PosNota: TNewStaticText;
  CasaGata, PosGata: TNewCheckBox;
  OriginEdit, PortEdit, ParolaEdit, Parola2Edit: TNewEdit;

function Port(Param: String): String;
begin
  Result := Trim(PortEdit.Text);
end;

procedure DeschideLink(Sender: TObject);
var
  Cod: Integer;
begin
  ShellExec('open', TNewStaticText(Sender).Caption, '', '', SW_SHOWNORMAL, ewNoWait, Cod);
end;

function FaLink(Pagina: TWizardPage; Sus: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(Pagina);
  Result.Parent := Pagina.Surface;
  Result.Left := 0;
  Result.Top := Sus;
  Result.Font.Color := clBlue;
  Result.Font.Style := [fsUnderline];
  Result.Cursor := crHand;
  Result.OnClick := @DeschideLink;
end;

function FaNota(Pagina: TWizardPage; Sus: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(Pagina);
  Result.Parent := Pagina.Surface;
  Result.Left := 0;
  Result.Top := Sus;
  Result.Width := Pagina.SurfaceWidth;
  Result.AutoSize := False;
  Result.WordWrap := True;
  Result.Height := ScaleY(48);
end;

procedure ActualizeazaButonul;
begin
  if not (Assigned(CasaPage) and Assigned(PosPage)) then Exit;   { se apelează și cât pagina POS încă nu e creată }
  if WizardForm.CurPageID = CasaPage.ID then
    WizardForm.NextButton.Enabled := CasaGata.Checked
  else if WizardForm.CurPageID = PosPage.ID then
    WizardForm.NextButton.Enabled := PosNone.Checked or PosGata.Checked;
end;

procedure CasaSchimbata(Sender: TObject);
begin
  if CasaFiscool.Checked then
  begin
    CasaLink.Caption := LINK_FISCOOL;
    CasaNota.Caption := 'Pe pagină: "Descarcă pentru Windows". Are 14 zile de probă; licența se activează în FisCool.';
  end else
  begin
    CasaLink.Caption := LINK_FISCO;
    CasaNota.Caption := 'Instalează driverul Fisco după manualul producătorului.';
  end;
  CasaGata.Checked := False;
  ActualizeazaButonul;
end;

procedure PosSchimbat(Sender: TObject);
begin
  PosLink.Visible := PosNewland.Checked;
  PosNota.Visible := PosNewland.Checked;
  PosGata.Visible := PosNewland.Checked;
  if PosNewland.Checked then
  begin
    PosLink.Caption := LINK_NEWLAND;
    PosNota.Caption := 'Arhiva .rar cu driverul USB al terminalului: dezarhiveaz-o și rulează programul de instalare.';
  end;
  PosGata.Checked := False;
  ActualizeazaButonul;
end;

procedure BifaSchimbata(Sender: TObject);
begin
  ActualizeazaButonul;
end;

procedure InitializeWizard;
begin
  { --- casa de marcat }
  CasaPage := CreateCustomPage(wpWelcome, 'Casa de marcat', 'Alege casa de marcat, instalează-i driverul, apoi bifează.');
  CasaFiscool := TNewRadioButton.Create(CasaPage);
  CasaFiscool.Parent := CasaPage.Surface;
  CasaFiscool.Caption := 'FisCool';
  CasaFiscool.Width := CasaPage.SurfaceWidth;
  CasaFiscool.Checked := True;
  CasaFiscool.OnClick := @CasaSchimbata;
  CasaFisco := TNewRadioButton.Create(CasaPage);
  CasaFisco.Parent := CasaPage.Surface;
  CasaFisco.Top := ScaleY(24);
  CasaFisco.Caption := 'Fisco';
  CasaFisco.Width := CasaPage.SurfaceWidth;
  CasaFisco.OnClick := @CasaSchimbata;
  CasaLink := FaLink(CasaPage, ScaleY(64));
  CasaNota := FaNota(CasaPage, ScaleY(86));
  CasaGata := TNewCheckBox.Create(CasaPage);
  CasaGata.Parent := CasaPage.Surface;
  CasaGata.Top := ScaleY(144);
  CasaGata.Width := CasaPage.SurfaceWidth;
  CasaGata.Caption := 'Am descărcat și instalat driverul casei de marcat';
  CasaGata.OnClick := @BifaSchimbata;
  CasaSchimbata(nil);

  { --- POS }
  PosPage := CreateCustomPage(CasaPage.ID, 'POS bancar', 'Alege POS-ul folosit de agent. Fără POS, plata cu cardul se face separat, pe terminal.');
  PosNone := TNewRadioButton.Create(PosPage);
  PosNone.Parent := PosPage.Surface;
  PosNone.Caption := 'Fără POS';
  PosNone.Width := PosPage.SurfaceWidth;
  PosNone.Checked := True;
  PosNone.OnClick := @PosSchimbat;
  PosNewland := TNewRadioButton.Create(PosPage);
  PosNewland.Parent := PosPage.Surface;
  PosNewland.Top := ScaleY(24);
  PosNewland.Caption := 'Raiffeisen / Newland N910 Pro (USB)';
  PosNewland.Width := PosPage.SurfaceWidth;
  PosNewland.OnClick := @PosSchimbat;
  PosLink := FaLink(PosPage, ScaleY(64));
  PosNota := FaNota(PosPage, ScaleY(86));
  PosGata := TNewCheckBox.Create(PosPage);
  PosGata.Parent := PosPage.Surface;
  PosGata.Top := ScaleY(144);
  PosGata.Width := PosPage.SurfaceWidth;
  PosGata.Caption := 'Am descărcat și instalat driverul POS-ului';
  PosGata.OnClick := @BifaSchimbata;
  PosSchimbat(nil);

  { --- aplicația și parola /setari }
  AppPage := CreateCustomPage(PosPage.ID, 'Aplicația', 'Adresa aplicației web care folosește agentul și parola paginii de setări.');
  with TNewStaticText.Create(AppPage) do begin Parent := AppPage.Surface; Top := 0; Caption := 'Adresa aplicației (ex. https://test.worldbit.ro)'; end;
  OriginEdit := TNewEdit.Create(AppPage);
  OriginEdit.Parent := AppPage.Surface; OriginEdit.Top := ScaleY(18); OriginEdit.Width := AppPage.SurfaceWidth;
  OriginEdit.Text := 'https://test.worldbit.ro';
  with TNewStaticText.Create(AppPage) do begin Parent := AppPage.Surface; Top := ScaleY(52); Caption := 'Portul agentului'; end;
  PortEdit := TNewEdit.Create(AppPage);
  PortEdit.Parent := AppPage.Surface; PortEdit.Top := ScaleY(70); PortEdit.Width := ScaleX(80); PortEdit.Text := '4747';
  with TNewStaticText.Create(AppPage) do begin Parent := AppPage.Surface; Top := ScaleY(104); Caption := 'Parola paginii /setari (minim 8 caractere)'; end;
  ParolaEdit := TNewEdit.Create(AppPage);
  ParolaEdit.Parent := AppPage.Surface; ParolaEdit.Top := ScaleY(122); ParolaEdit.Width := ScaleX(220); ParolaEdit.PasswordChar := '*';
  with TNewStaticText.Create(AppPage) do begin Parent := AppPage.Surface; Top := ScaleY(156); Caption := 'Repetă parola'; end;
  Parola2Edit := TNewEdit.Create(AppPage);
  Parola2Edit.Parent := AppPage.Surface; Parola2Edit.Top := ScaleY(174); Parola2Edit.Width := ScaleX(220); Parola2Edit.PasswordChar := '*';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  ActualizeazaButonul;
  if CurPageID = AppPage.ID then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall)
  else if CurPageID <> wpFinished then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonNext);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Nr: Integer;
  Adresa: String;
begin
  Result := True;
  if CurPageID = AppPage.ID then
  begin
    Adresa := Trim(OriginEdit.Text);
    if (Pos('http://', Adresa) <> 1) and (Pos('https://', Adresa) <> 1) or (Pos(' ', Adresa) > 0) or (Pos('/', Copy(Adresa, 9, 200)) > 0) then
    begin
      MsgBox('Adresa aplicației trebuie să fie de forma https://test.worldbit.ro (fără cale după nume).', mbError, MB_OK);
      Result := False; Exit;
    end;
    Nr := StrToIntDef(Trim(PortEdit.Text), 0);
    if (Nr < 1) or (Nr > 65535) then
    begin
      MsgBox('Port invalid.', mbError, MB_OK);
      Result := False; Exit;
    end;
    if Length(ParolaEdit.Text) < 8 then
    begin
      MsgBox('Parola trebuie să aibă minim 8 caractere.', mbError, MB_OK);
      Result := False; Exit;
    end;
    if ParolaEdit.Text <> Parola2Edit.Text then
    begin
      MsgBox('Parolele nu coincid.', mbError, MB_OK);
      Result := False; Exit;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Casa, Pos_, Comanda, Jurnal, Continut: String;
  Cod: Integer;
  Linii: AnsiString;
begin
  if CurStep <> ssPostInstall then Exit;
  if CasaFiscool.Checked then Casa := 'fiscool' else Casa := 'fisco';
  if PosNewland.Checked then Pos_ := 'hypercom_ecr' else Pos_ := 'none';
  Jurnal := ExpandConstant('{tmp}\install.log');
  SetEnvironmentVariable('WORLDBIT_ADMIN_PASSWORD', ParolaEdit.Text);
  WizardForm.StatusLabel.Caption := 'Se instalează agentul (Python, fișiere, pornire automată)...';
  Comanda := '/c ""powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}') + '\installer\install.ps1" -Driver ' + Casa +
             ' -Pos ' + Pos_ + ' -Origin ' + Trim(OriginEdit.Text) + ' -Port ' + Trim(PortEdit.Text) + ' > "' + Jurnal + '" 2>&1"';
  if (not Exec(ExpandConstant('{cmd}'), Comanda, '', SW_HIDE, ewWaitUntilTerminated, Cod)) or (Cod <> 0) then
  begin
    Continut := '';
    if LoadStringFromFile(Jurnal, Linii) then Continut := Copy(String(Linii), 1, 1500);
    SetEnvironmentVariable('WORLDBIT_ADMIN_PASSWORD', '');
    MsgBox('Instalarea agentului a eșuat (cod ' + IntToStr(Cod) + ').' + #13#10#13#10 + Continut, mbError, MB_OK);
    Abort;
  end;
  SetEnvironmentVariable('WORLDBIT_ADMIN_PASSWORD', '');
end;
