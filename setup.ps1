# Gemini 4 Carbon, Argon and the other Gemini models on your key, in Google Antigravity (agy CLI + desktop app),
# with your own Gemini API key. Windows (PowerShell 5.1 and 7).
#   .\setup.ps1                      install (asks for your key once, keeps it in the Windows Credential Manager)
#   .\setup.ps1 -Models a,b,c        install with exactly these models in Antigravity's model menu
#   .\setup.ps1 -Remove              undo everything
# What it installs: the local relay (relay.py, 127.0.0.1:8918, starts at logon), the gateway settings as user
# environment variables (the desktop app reads them after a restart), and an `agy` function in your PowerShell profile.
# Your key is never written into a settings file.
# If Windows refuses to run scripts: powershell -ExecutionPolicy Bypass -File .\setup.ps1
param([string]$Models = "", [switch]$Remove)
$ErrorActionPreference = "Stop"
$Dir = $PSScriptRoot; $Port = 8918; $Task = "agy-gemini4 relay"; $Mark = "# agy-gemini4"; $Target = "agy-gemini4"
$HomeDir = Join-Path $HOME ".agy-gemini4"
$Vars = "AGY_LLM_GATEWAY_URL", "AGY_LLM_GATEWAY_MODELS", "AGY_LLM_GATEWAY_API_KEY"
$Docs = [Environment]::GetFolderPath("MyDocuments")
$Profiles = (Join-Path $Docs "WindowsPowerShell\Microsoft.PowerShell_profile.ps1"),   # PowerShell 5.1
            (Join-Path $Docs "PowerShell\Microsoft.PowerShell_profile.ps1")            # PowerShell 7

# Windows Credential Manager (generic credential), through the Win32 API: no module to install
if (-not ("AgyGemini4.Cred" -as [type])) {
  Add-Type -Namespace AgyGemini4 -Name Cred -MemberDefinition @'
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct CREDENTIAL {
  public int Flags; public int Type; public string TargetName; public string Comment;
  public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
  public int CredentialBlobSize; public IntPtr CredentialBlob; public int Persist; public int AttributeCount;
  public IntPtr Attributes; public string TargetAlias; public string UserName;
}
[DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
static extern bool CredWriteW(ref CREDENTIAL c, int flags);
[DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
static extern bool CredReadW(string target, int type, int flags, out IntPtr c);
[DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
static extern bool CredDeleteW(string target, int type, int flags);
[DllImport("advapi32.dll")]
static extern void CredFree(IntPtr c);
public static void Write(string target, string user, string secret) {
  byte[] b = System.Text.Encoding.Unicode.GetBytes(secret);
  CREDENTIAL c = new CREDENTIAL();
  c.Type = 1; c.TargetName = target; c.UserName = user; c.Persist = 2;   // generic, local machine
  c.CredentialBlobSize = b.Length; c.CredentialBlob = Marshal.AllocHGlobal(b.Length);
  try {
    Marshal.Copy(b, 0, c.CredentialBlob, b.Length);
    if (!CredWriteW(ref c, 0)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
  } finally { Marshal.FreeHGlobal(c.CredentialBlob); }
}
public static string Read(string target) {
  IntPtr p;
  if (!CredReadW(target, 1, 0, out p)) return null;
  try {
    CREDENTIAL c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
    return c.CredentialBlobSize == 0 ? "" : Marshal.PtrToStringUni(c.CredentialBlob, c.CredentialBlobSize / 2);
  } finally { CredFree(p); }
}
public static bool Delete(string target) { return CredDeleteW(target, 1, 0); }
'@
}

function Remove-ProfileLines {
  foreach ($p in $Profiles) {
    if (Test-Path $p) {
      $keep = @(Get-Content $p | Where-Object { $_ -notmatch [regex]::Escape($Mark) })
      Set-Content -Path $p -Value $keep -Encoding UTF8
    }
  }
}

# clean start: stop the relay task, clear the app settings and the profile function (as setup.sh does)
if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $Task -Confirm:$false
}
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like "*relay.py*--port*$Port*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($v in $Vars) { [Environment]::SetEnvironmentVariable($v, $null, "User") }
Remove-ProfileLines
if ($Remove) {
  [void][AgyGemini4.Cred]::Delete($Target)
  $ep = Join-Path $HomeDir "execution_policy_set"
  if (Test-Path $ep) {   # undo the execution policy change only if this installer made it
    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Undefined -Force -ErrorAction SilentlyContinue
    Remove-Item $ep
  }
  Write-Host "Removed. Quit and reopen Antigravity."; exit 0
}

# a real Python 3 (not the Microsoft Store placeholder that only opens the Store)
$Py = $null
foreach ($c in @(@("py", "-3"), @("python"), @("python3"))) {
  if (Get-Command $c[0] -CommandType Application -ErrorAction SilentlyContinue) {
    try { $exe = & $c[0] @($c | Select-Object -Skip 1) -c "import sys; print(sys.executable)" 2>$null } catch { $exe = $null }
    if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path "$exe")) { $Py = "$exe".Trim(); break }
  }
}
if (-not $Py) { Write-Host "Python 3 not found (install it from python.org, then open a new PowerShell)"; exit 1 }
$PyW = Join-Path (Split-Path $Py) "pythonw.exe"; if (-not (Test-Path $PyW)) { $PyW = $Py }   # no console window

if ($null -eq [AgyGemini4.Cred]::Read($Target)) {
  $Key = $env:GEMINI_API_KEY
  if (-not $Key) {
    $s = Read-Host "Paste your Gemini API key (aistudio.google.com/apikey)" -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { $Key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
  }
  $Key = "$Key".Trim()
  if (-not $Key) { Write-Host "No key given."; exit 1 }
  [AgyGemini4.Cred]::Write($Target, $env:USERNAME, $Key)
}
$Key = [AgyGemini4.Cred]::Read($Target)

Write-Host "Checking which Gemini models your key can use..."
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
try {
  $List = Invoke-RestMethod -UseBasicParsing -Headers @{ "x-goog-api-key" = $Key } `
    -Uri "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
  $names = @($List.models | Where-Object { $_.supportedGenerationMethods -contains "generateContent" } |
    ForEach-Object { ($_.name -split "/")[-1] })
} catch { $names = @() }
$Key = $null
# default menu: every Gemini 4 model on the key (Carbon first) at three efforts, plus the newest Flash model
$g4 = @($names | Where-Object { $_ -cmatch "^gemini-4-[a-z]+$" } |
  Sort-Object @{ Expression = { $_ -ne "gemini-4-carbon" } }, @{ Expression = { $_ } })
$flash = @($names | Where-Object { $_ -cmatch "^gemini-\d+\.\d+-flash$" } |
  Sort-Object @{ Expression = { [int]($_ -replace "^gemini-(\d+)\.\d+-flash$", '$1') } },
              @{ Expression = { [int]($_ -replace "^gemini-\d+\.(\d+)-flash$", '$1') } })
# medium (plain name), low, high; Hydrogen and Neon also xhigh (Carbon refuses it, tested 2026-11-15)
$menu = @(foreach ($n in $g4) { $n; "$n-low"; "$n-high"; if ($n -in @("gemini-4-hydrogen", "gemini-4-neon")) { "$n-xhigh" } })
if ($flash.Count) { $menu += $flash[-1] }
$Found = $menu -join ","
if (-not $Found) { Write-Host "  no Gemini model found on this key (is billing on for its project?)"; exit 1 }
if (-not $Models) { $Models = $Found }
Write-Host "  models for Antigravity: $Models"

New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
# agy must talk to the Gemini API directly (modelProvider "gemini"); the old settings file is kept as settings.json.bak
$S = Join-Path $HOME ".gemini\antigravity-cli\settings.json"
New-Item -ItemType Directory -Force -Path (Split-Path $S) | Out-Null
if (Test-Path $S) { Copy-Item $S "$S.bak" -Force }
# (no double quotes in this Python: PowerShell 5.1 drops them from native command arguments)
& $Py -c @'
import json, os, sys
p = sys.argv[1]; d = json.load(open(p, encoding='utf-8-sig')) if os.path.exists(p) else {}
d['modelProvider'] = 'gemini'; d.setdefault('model', 'gemini-4-carbon')
json.dump(d, open(p, 'w', encoding='utf-8'), indent=2)
'@ $S
if ($LASTEXITCODE -ne 0) { Write-Host "Could not update $S"; exit 1 }

# the relay at logon (hidden, restarted if it stops), then started now
$act = New-ScheduledTaskAction -Execute $PyW -Argument "`"$Dir\relay.py`" --port $Port" -WorkingDirectory $Dir
$trg = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$prn = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $Task -Action $act -Trigger $trg -Settings $set -Principal $prn `
  -Description "agy-gemini4: Gemini API relay for Antigravity on 127.0.0.1:$Port" | Out-Null
Start-ScheduledTask -TaskName $Task

# the desktop app reads these at start (user environment variables, like the macOS LaunchAgent)
[Environment]::SetEnvironmentVariable("AGY_LLM_GATEWAY_URL", "http://127.0.0.1:$Port", "User")
[Environment]::SetEnvironmentVariable("AGY_LLM_GATEWAY_MODELS", $Models, "User")
[Environment]::SetEnvironmentVariable("AGY_LLM_GATEWAY_API_KEY", "relay", "User")

# `agy` in PowerShell: the same settings as the zsh function, for the CLI only
$fn = "function agy { `$v = @{ GEMINI_API_KEY = `$(if (`$env:GEMINI_API_KEY) { `$env:GEMINI_API_KEY } else { 'relay' }); " +
      "GOOGLE_GEMINI_BASE_URL = 'http://127.0.0.1:$Port'; AGY_LLM_GATEWAY_URL = 'http://127.0.0.1:$Port'; " +
      "AGY_LLM_GATEWAY_MODELS = '$Models'; AGY_LLM_GATEWAY_API_KEY = 'relay' }; `$o = @{}; " +
      "foreach (`$k in `$v.Keys) { `$o[`$k] = [Environment]::GetEnvironmentVariable(`$k, 'Process'); [Environment]::SetEnvironmentVariable(`$k, `$v[`$k], 'Process') }; " +
      "try { `$c = Get-Command agy -CommandType Application, ExternalScript -ErrorAction Stop | Select-Object -First 1; & `$c.Source @args } " +
      "finally { foreach (`$k in `$o.Keys) { [Environment]::SetEnvironmentVariable(`$k, `$o[`$k], 'Process') } } } $Mark"
foreach ($p in $Profiles) {
  New-Item -ItemType Directory -Force -Path (Split-Path $p) | Out-Null
  if ((Test-Path $p) -and (Get-Content $p -Raw) -and -not (Get-Content $p -Raw).EndsWith("`n")) { Add-Content -Path $p -Value "" }
  Add-Content -Path $p -Value $fn -Encoding UTF8
}
# profiles only load when scripts may run; Windows' default for clients is Restricted. Judge the policy a NEW window
# gets: without the Process scope, which `powershell -ExecutionPolicy Bypass -File .\setup.ps1` sets to Bypass for this run only
$NewWindowPolicy = @("MachinePolicy", "UserPolicy", "CurrentUser", "LocalMachine" | ForEach-Object { "$(Get-ExecutionPolicy -Scope $_)" }) |
  Where-Object { $_ -ne "Undefined" } | Select-Object -First 1
if (-not $NewWindowPolicy) { $NewWindowPolicy = if ((Get-CimInstance Win32_OperatingSystem).ProductType -eq 1) { "Restricted" } else { "RemoteSigned" } }
if ((Get-ExecutionPolicy -Scope CurrentUser) -eq "Undefined" -and $NewWindowPolicy -in "Restricted", "AllSigned") {
  # When this run is itself Bypass (powershell -ExecutionPolicy Bypass -File ...), Set-ExecutionPolicy saves the
  # CurrentUser value AND throws "overridden by a policy defined at a more specific scope". Judge by reading it back.
  try { Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force -ErrorAction Stop } catch { }
  if ((Get-ExecutionPolicy -Scope CurrentUser) -eq "RemoteSigned") {
    New-Item -ItemType File -Force -Path (Join-Path $HomeDir "execution_policy_set") | Out-Null
    Write-Host "PowerShell may now run your profile (execution policy RemoteSigned for your user)."
  } else { Write-Host "Your execution policy blocks the PowerShell profile: allow it with Set-ExecutionPolicy -Scope CurrentUser RemoteSigned, then open a new window." }
}

$ok = $false
foreach ($i in 1..10) {
  Start-Sleep -Milliseconds 700
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$Port/v1beta/models" | Out-Null; $ok = $true; break }
  catch { if ($_.Exception.Response) { $ok = $true; break } }
}
if ($ok) { Write-Host "Relay running on 127.0.0.1:$Port." } else { Write-Host "Relay did not answer yet; log: $HomeDir\relay.log" }
Write-Host "Done. Open a new PowerShell and run agy, then /model. Quit and reopen the Antigravity app."
