#!/bin/sh
# Gemini 4 Carbon, Argon and the other Gemini models on your key, in Google Antigravity (agy CLI + desktop app),
# with your own Gemini API key. macOS.
#   ./setup.sh                      install (asks for your key once, keeps it in the macOS Keychain)
#   ./setup.sh --models a,b,c       install with exactly these models in Antigravity's model menu
#   ./setup.sh --remove             undo everything
# What it installs: the local relay (relay.py, 127.0.0.1:8918, starts at login), the app settings at login, and an
# `agy` shell function in ~/.zshrc. Your key is never written into a settings file.
set -e
DIR=$(cd "$(dirname "$0")" && pwd); PORT=8918; LA="$HOME/Library/LaunchAgents"; D="gui/$(id -u)"
RELAY=net.agy-gemini4.relay; ENV=net.agy-gemini4.env; RC="$HOME/.zshrc"; MARK="# agy-gemini4"
MODELS=""; [ "$1" = "--models" ] && MODELS="$2"; HOME_DIR="$HOME/.agy-gemini4"

for l in $RELAY $ENV; do launchctl bootout "$D/$l" 2>/dev/null || true; rm -f "$LA/$l.plist"; done
for v in AGY_LLM_GATEWAY_URL AGY_LLM_GATEWAY_MODELS AGY_LLM_GATEWAY_API_KEY; do launchctl unsetenv $v; done
[ -f "$RC" ] && sed -i '' "/$MARK/d" "$RC"
if [ "$1" = "--remove" ]; then
  security delete-generic-password -s agy-gemini4 >/dev/null 2>&1 || true
  echo "Removed. Quit and reopen Antigravity."; exit 0
fi

PY=$(command -v python3) || { echo "python3 not found (install it from python.org)"; exit 1; }
if ! security find-generic-password -s agy-gemini4 -w >/dev/null 2>&1; then
  KEY="$GEMINI_API_KEY"
  if [ -z "$KEY" ]; then printf "Paste your Gemini API key (aistudio.google.com/apikey): "; stty -echo; read -r KEY; stty echo; echo; fi
  security add-generic-password -U -s agy-gemini4 -a "$USER" -w "$KEY"
fi
KEY=$(security find-generic-password -s agy-gemini4 -w)

echo "Checking which Gemini models your key can use..."
LIST=$(curl -s "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000" -H "x-goog-api-key: $KEY")
# default menu: every Gemini 4 model on the key (Carbon first) at three efforts, plus the newest Flash model
FOUND=$(echo "$LIST" | "$PY" -c '
import json, re, sys
try: ms = json.load(sys.stdin).get("models", [])
except ValueError: ms = []
names = [m["name"].split("/")[-1] for m in ms if "generateContent" in m.get("supportedGenerationMethods", [])]
g4 = sorted((n for n in names if re.fullmatch(r"gemini-4-[a-z]+", n)), key=lambda n: (n != "gemini-4-carbon", n))
flash = sorted((n for n in names if re.fullmatch(r"gemini-\d+\.\d+-flash", n)), key=lambda n: [int(x) for x in re.findall(r"\d+", n)])
g4 = [v for n in g4 for v in (n, n + "-low", n + "-high")]   # each model at medium (its plain name), low and high: agy and the app show them as efforts
print(",".join(g4 + flash[-1:]))')
[ -n "$FOUND" ] || { echo "  no Gemini model found on this key (is billing on for its project?)"; exit 1; }
[ -n "$MODELS" ] || MODELS="$FOUND"
echo "  models for Antigravity: $MODELS"
KEY=

mkdir -p "$LA" "$HOME_DIR"
# agy must talk to the Gemini API directly (modelProvider "gemini"); the old settings file is kept as settings.json.bak
S="$HOME/.gemini/antigravity-cli/settings.json"; mkdir -p "$(dirname "$S")"; [ -f "$S" ] && cp "$S" "$S.bak"
"$PY" - "$S" <<'PYEOF'
import json, os, sys
p = sys.argv[1]; d = json.load(open(p)) if os.path.exists(p) else {}
d["modelProvider"] = "gemini"; d.setdefault("model", "gemini-4-carbon")
json.dump(d, open(p, "w"), indent=2)
PYEOF
cat > "$LA/$RELAY.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$RELAY</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/relay.py</string><string>--port</string><string>$PORT</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$HOME_DIR/relay.log</string>
</dict></plist>
EOF
cat > "$LA/$ENV.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$ENV</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string>
    <string>launchctl setenv AGY_LLM_GATEWAY_URL http://127.0.0.1:$PORT; launchctl setenv AGY_LLM_GATEWAY_MODELS $MODELS; launchctl setenv AGY_LLM_GATEWAY_API_KEY relay</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
launchctl bootstrap "$D" "$LA/$RELAY.plist"; launchctl bootstrap "$D" "$LA/$ENV.plist"
echo "agy() { GEMINI_API_KEY=\${GEMINI_API_KEY:-relay} GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:$PORT AGY_LLM_GATEWAY_URL=http://127.0.0.1:$PORT AGY_LLM_GATEWAY_MODELS=$MODELS AGY_LLM_GATEWAY_API_KEY=relay command agy \"\$@\"; } $MARK" >> "$RC"
sleep 1; curl -s -o /dev/null "http://127.0.0.1:$PORT/v1beta/models" && echo "Relay running on 127.0.0.1:$PORT."
echo "Done. Open a new terminal and run agy, then /model. Quit and reopen the Antigravity app."
