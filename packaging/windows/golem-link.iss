#define MyAppName "GOLEM LINK"
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#define MyAppPublisher "GOLEM"
#define MyServiceName "golem-link"

[Setup]
AppId={{7C4B2D9A-3E5F-4A61-B8D2-9F1C6E8A4D03}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\GOLEM LINK
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\build\windows
OutputBaseFilename=GOLEM-LINK-Setup-Windows
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0.17763
UninstallDisplayName={#MyAppName}
CloseApplications=yes
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start GOLEM LINK automatically as a Windows service"; GroupDescription: "Service:"; Flags: checkedonce
Name: "firewall"; Description: "Allow GOLEM LINK through the Windows firewall on Private networks only (TCP 8080, TCP 8443, UDP 45873, UDP 5353)"; GroupDescription: "Network:"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut to the dashboard"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\GOLEM-LINK\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\build\windows\WinSW-x64.exe"; DestDir: "{app}"; DestName: "golem-link-service.exe"; Flags: ignoreversion
Source: "golem-link-service.xml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\config.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\docs\*.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\..\packaging\windows\install-service.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\..\packaging\windows\uninstall-service.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Icons]
Name: "{group}\GOLEM LINK Dashboard"; Filename: "http://localhost:8080/"
Name: "{group}\Install or repair the service"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\install-service.ps1"""; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\GOLEM LINK Dashboard"; Filename: "http://localhost:8080/"; Tasks: desktopicon

[Run]
Filename: "{app}\golem-link-service.exe"; Parameters: "install"; Flags: runhidden waituntilterminated; StatusMsg: "Registering GOLEM LINK service..."; Tasks: autostart
Filename: "{app}\golem-link-service.exe"; Parameters: "start"; Flags: runhidden waituntilterminated; StatusMsg: "Starting GOLEM LINK service..."; Tasks: autostart
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GOLEM LINK Dashboard"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GOLEM LINK Dashboard"" dir=in action=allow protocol=TCP localport=8080 profile=private remoteip=localsubnet"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GOLEM LINK Peer API"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GOLEM LINK Peer API"" dir=in action=allow protocol=TCP localport=8443 profile=private remoteip=localsubnet"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GOLEM LINK Discovery"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GOLEM LINK Discovery"" dir=in action=allow protocol=UDP localport=45873 profile=private remoteip=localsubnet"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GOLEM LINK mDNS"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GOLEM LINK mDNS"" dir=in action=allow protocol=UDP localport=5353 profile=private remoteip=localsubnet"; Flags: runhidden; Tasks: firewall
Filename: "http://localhost:8080/"; Description: "Open the GOLEM LINK dashboard"; Flags: postinstall shellexec nowait

[UninstallRun]
Filename: "{app}\golem-link-service.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{app}\golem-link-service.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GOLEM LINK Dashboard"""; Flags: runhidden; RunOnceId: "FwDash"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GOLEM LINK Peer API"""; Flags: runhidden; RunOnceId: "FwPeer"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GOLEM LINK Discovery"""; Flags: runhidden; RunOnceId: "FwDisc"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GOLEM LINK mDNS"""; Flags: runhidden; RunOnceId: "FwMdns"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\docs"
