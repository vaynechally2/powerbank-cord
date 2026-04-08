[Setup]
AppName=BraveClaimBotV4
AppVersion=4.0
DefaultDirName={autopf}\BraveClaimBotV4
DefaultGroupName=BraveClaimBotV4
OutputBaseFilename=BraveClaimBotV4_Installer
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\brave_claim_v4.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "profiles.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Dirs]
Name: "{app}\screenshots"
Name: "{app}\logs"

[Icons]
Name: "{group}\Brave Claim Bot V4"; Filename: "{app}\brave_claim_v4.exe"
Name: "{commondesktop}\Brave Claim Bot V4"; Filename: "{app}\brave_claim_v4.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\brave_claim_v4.exe"; Description: "Launch Brave Claim Bot V4"; Flags: nowait postinstall skipifsilent
