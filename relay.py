"""agy-gemini4 relay: lets Google Antigravity (the agy CLI and the desktop app) use Gemini 4 Carbon and Gemini 4 Argon
with your own Gemini API key.

Why it is needed: on an API key, Antigravity lists only older models. Its gateway mode (AGY_LLM_GATEWAY_URL +
AGY_LLM_GATEWAY_MODELS) can offer Gemini 4, but it sends the model as "gemini-4-carbon-high" (effort glued on), which
the Gemini API rejects with 404, and the desktop app sends the key as a Bearer token, which the API rejects with 401.
This relay runs on your computer (127.0.0.1 only), fixes those two things, forwards everything else unchanged to
generativelanguage.googleapis.com, and writes Google's token counts for every call to a local usage file.

Your key is read from GEMINI_API_KEY, the macOS Keychain or the Windows Credential Manager (item "agy-gemini4"), never
written into app settings.
`python3 relay.py --cost` prints what your calls cost at Google's list price.
"""
import argparse, json, os, re, ssl, sys, time, urllib.error, urllib.request
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "https://generativelanguage.googleapis.com"
# macOS: the system CA file; Windows and Linux: the default store (Windows reads its own certificate store)
CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if os.path.exists("/etc/ssl/cert.pem") else ssl.create_default_context()
MODEL_RE = re.compile(r"models/(gemini-[a-z0-9.\-]+?)-(low|medium|high|xhigh)(?=[:/?]|$)")  # effort glued to the name (xhigh: Hydrogen and Neon)
HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
def _windows_credential(target="agy-gemini4"):
    """The password of the generic credential `target` in the Windows Credential Manager (setup.ps1 writes it), or ""."""
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    p = ctypes.POINTER(CREDENTIAL)()
    if not adv.CredReadW(target, 1, 0, ctypes.byref(p)):   # 1 = CRED_TYPE_GENERIC
        return ""
    try:
        blob = ctypes.string_at(p.contents.CredentialBlob, p.contents.CredentialBlobSize)
    finally:
        adv.CredFree(p)
    # setup.ps1 and cmdkey store the password as UTF-16LE; accept UTF-8 too
    try:
        text = blob.decode("utf-16-le") if len(blob) % 2 == 0 and b"\x00" in blob else blob.decode("utf-8")
    except UnicodeDecodeError:
        text = blob.decode("utf-8", "replace")
    return text.strip()


def _key():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if os.name == "nt":
        try:
            return _windows_credential()
        except (OSError, AttributeError, ValueError):
            return ""
    try:
        import subprocess
        return subprocess.run(["security", "find-generic-password", "-s", "agy-gemini4", "-w"],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


KEY = _key()
THINKING_OVERRIDE = Path.home() / ".agy-gemini4" / "thinking"
KEEP_OUTPUT_LIMIT = Path.home() / ".agy-gemini4" / "keep_output_limit"
CAP_FILE = Path.home() / ".agy-gemini4" / "daily_cap"   # a dollar amount: model calls are refused once today's spend reaches it
LABEL_FILE = Path.home() / ".agy-gemini4" / "label"     # text attached to every call logged while it exists (a run name)
_spend = {"key": None, "usd": 0.0}


def read_cap():
    try:
        return float(CAP_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def today_spend(ledger):
    """List-price dollars of today's calls (local time), recomputed only when the ledger grows."""
    if not ledger or not os.path.exists(ledger):
        return 0.0
    key = (os.path.getsize(ledger), time.strftime("%Y-%m-%d"))
    if _spend["key"] != key:
        day, usd = key[1], 0.0
        for line in open(ledger):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if time.strftime("%Y-%m-%d", time.localtime(r.get("t", 0))) == day:
                cc = call_cost(r)
                usd += (cc[3] or 0) if cc else 0
        _spend.update(key=key, usd=usd)
    return _spend["usd"]
MIN_OUTPUT = 65536   # Gemini 4 Carbon and Argon can write 65K tokens in one reply


def raise_output_limit(j, path):
    """agy asks gateway models for only 8,192 output tokens. At high effort the model's thinking uses most of that, and a
    long file gets cut off ("exceeded the output token limit", measured on agy 1.5.5 with Carbon, 2026-11-11). For Gemini 4
    models the limit is raised to MIN_OUTPUT unless the file ~/.agy-gemini4/keep_output_limit exists. Returns what was asked."""
    gc = j.setdefault("generationConfig", {})
    asked = gc.get("maxOutputTokens")
    if "gemini-4-" in path and not KEEP_OUTPUT_LIMIT.exists() and (asked is None or asked < MIN_OUTPUT):
        gc["maxOutputTokens"] = MIN_OUTPUT
    return asked


class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _forward(self):
        if self.command == "GET" and self.path.split("?")[0] in ("/", "/dashboard"):
            return self._dashboard()
        t0 = time.time()
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        cap = read_cap()
        if cap is not None and "generatecontent" in self.path.lower() and today_spend(self.server.ledger) >= cap:
            spent = today_spend(self.server.ledger)
            msg = (f"agy-gemini4: daily spend cap reached (${spent:.2f} today at list price, cap ${cap:.2f}). "
                   f"Raise it with: python3 relay.py --cap <dollars>, or remove it with --cap off.")
            out = json.dumps({"error": {"code": 429, "message": msg, "status": "RESOURCE_EXHAUSTED"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            if self.server.ledger:
                with open(self.server.ledger, "a") as f:
                    f.write(json.dumps({"t": round(t0, 3), "seconds": 0, "status": 429, "path": self.path.split("?")[0],
                                        "capped": True, "usage": None}) + "\n")
            return
        path, level = self.path, None
        m = MODEL_RE.search(path)
        if m:
            level = m.group(2)
            path = MODEL_RE.sub(r"models/\1", path)
        req_info = {}
        if body and b"enhanced_content" in body:
            # Antigravity 2.x sends google_search.enhanced_content.imageSearch, which the Gemini API rejects (400)
            try:
                j = json.loads(body)
                for t in j.get("tools") or []:
                    for k in ("google_search", "googleSearch"):
                        if isinstance(t.get(k), dict):
                            t[k].pop("enhanced_content", None)
                            t[k].pop("enhancedContent", None)
                body = json.dumps(j).encode()
            except ValueError:
                pass
        if body and level:
            try:
                j = json.loads(body)
                gc = j.setdefault("generationConfig", {})
                tc = gc.setdefault("thinkingConfig", {})
                tc.pop("thinkingBudget", None)
                tc["thinkingLevel"] = level
                asked = raise_output_limit(j, path)
                req_info = {"maxOutputTokens": gc.get("maxOutputTokens"), "maxOutputTokensAsked": asked, "thinkingLevel": level}
                body = json.dumps(j).encode()
            except ValueError:
                pass
        elif body and "generatecontent" in path.lower():
            # other clients (opencode, the Antigravity app) set the effort themselves; record what they asked for.
            # The app has no effort picker on a gateway model and always asks HIGH (10 to 20 s a step, measured); a word in THINKING_OVERRIDE (low, medium, high) replaces it for Argon calls.
            try:
                j = json.loads(body)
                tcfg = j.get("generationConfig", {}).get("thinkingConfig", {})
                if str(tcfg.get("thinkingLevel", "")).upper() == "MINIMAL" and "gemini-3.8-flash" in path:
                    tcfg["thinkingLevel"] = "LOW"   # agy's side calls ask MINIMAL, which gemini-3.8-flash rejects (400)
                    body = json.dumps(j).encode()
                over = THINKING_OVERRIDE.read_text().strip().upper() if THINKING_OVERRIDE.exists() else ""
                if over and ("gemini-4-argon" in path or "gemini-4-carbon" in path):
                    j.setdefault("generationConfig", {}).setdefault("thinkingConfig", {})["thinkingLevel"] = over
                    j["generationConfig"]["thinkingConfig"].pop("thinkingBudget", None)
                    body = json.dumps(j).encode()
                asked = raise_output_limit(j, path)
                body = json.dumps(j).encode()
                gc = j.get("generationConfig", {})
                tc = gc.get("thinkingConfig", {})
                req_info = {"maxOutputTokens": gc.get("maxOutputTokens"), "maxOutputTokensAsked": asked,
                            "thinkingLevel": tc.get("thinkingLevel"), "thinkingBudget": tc.get("thinkingBudget"),
                            # which tools the client offered the model (to debug MCP tools that never reach it)
                            "tools": sorted(f.get("name", "?") for t in j.get("tools") or []
                                            for f in t.get("functionDeclarations") or t.get("function_declarations") or [])}
            except ValueError:
                pass
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        # agy's side calls (titles, summaries) ask for an 8 s deadline; the Gemini API rejects anything under 10 s (400)
        for k, v in list(headers.items()):
            if "timeout" in k.lower() and re.fullmatch(r"\d+(\.\d+)?", v.strip()) and float(v) < 10:
                headers[k] = "10"
        # the Antigravity app's gateway mode sends the key as "Authorization: Bearer <key>"; the Gemini API wants
        # API keys in x-goog-api-key (Bearer is for OAuth tokens only, 401 otherwise)
        for k, v in list(headers.items()):
            if k.lower() == "authorization" and v.startswith(("Bearer AIza", "Bearer AQ.")):  # old and new key formats
                del headers[k]
                headers["x-goog-api-key"] = v[len("Bearer "):]
        # clients set up with the placeholder key "relay" (setup.sh) get the key from this process's
        # GEMINI_API_KEY, so it is never written into their settings
        for k, v in list(headers.items()):
            if k.lower() in ("authorization", "x-goog-api-key") and v in ("Bearer relay", "relay"):
                del headers[k]
        if KEY and not any(k.lower() in ("authorization", "x-goog-api-key") for k in headers) and "key=" not in path:
            headers["x-goog-api-key"] = KEY
        req = urllib.request.Request(UPSTREAM + path, data=body or None, headers=headers, method=self.command)
        try:
            resp = urllib.request.urlopen(req, timeout=1800, context=CTX)
            status = resp.status
        except urllib.error.HTTPError as e:
            resp, status = e, e.code
        self.send_response(status)
        for k, v in resp.headers.items():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        usage, tail, client_closed = None, b"", False
        upstream_error = None
        while True:
            try:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            except (OSError, http.client.HTTPException) as e:
                # Google dropped the stream mid-reply (seen: "Connection reset by peer"). End the reply cleanly so the
                # client sees a finished response and retries, instead of waiting on a connection that never closes.
                upstream_error = f"{type(e).__name__}: {e}"
                break
            if not chunk:
                break
            tail = (tail + chunk)[-200000:]
            try:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # the client gave up mid-stream (agy cancels some calls); still log the usage Google reported so far
                client_closed = True
                resp.close()
                break
        if not client_closed:
            try:
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                client_closed = True
        for u in re.findall(rb'"usageMetadata"\s*:\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', tail):
            try:
                usage = json.loads(u)  # the last one in a stream is the cumulative total
            except ValueError:
                pass
        if status == 400 and body and self.server.ledger:
            # keep the rejected request (no key: it travels in a header) so a 400 can be diagnosed
            dump = os.path.join(os.path.dirname(self.server.ledger), "rejected", f"{int(t0)}.json")
            os.makedirs(os.path.dirname(dump), exist_ok=True)
            with open(dump, "wb") as f:
                f.write(body)
        if self.server.ledger and ("generateContent" in path or "streamGenerateContent" in path):
            with open(self.server.ledger, "a") as f:
                f.write(json.dumps({"t": round(t0, 3), "seconds": round(time.time() - t0, 2), "status": status,
                                    "path": path.split("?")[0], "agy_model": m.group(0) if m else None,
                                    **req_info, "usage": usage, **({"client_closed": True} if client_closed else {}),
                                    **({"label": LABEL_FILE.read_text().strip()} if LABEL_FILE.exists() else {}),
                                    **({"upstream_error": upstream_error} if upstream_error else {}),
                                    **({"error": tail[-600:].decode("utf-8", "replace")} if status >= 400 else {})}) + "\n")

    def _dashboard(self):
        rows = []
        if self.server.ledger and os.path.exists(self.server.ledger):
            rows = [json.loads(l) for l in open(self.server.ledger) if l.strip()]
        html = dashboard_html(rows).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    do_GET = do_POST = _forward


# list price per 1M tokens (aistudio.google.com/docs/pricing): input, cached input, output incl. thinking
PRICE = {"gemini-4-carbon": (2.00, 0.10, 10.00), "gemini-4-argon": (2.00, 0.10, 10.00),  # until 2026-11-30; x2 after
         "gemini-4-hydrogen": (1.50, 0.075, 7.50),                                       # no change announced
         "gemini-4-neon": (3.00, 0.15, 15.00)}                                           # 2026-11-18, no intro price


def call_cost(r):
    """(model, input tokens, output tokens, usd or None when no price is known) for one usage row, or None."""
    u, m = r.get("usage") or {}, re.search(r"models/([^:/?]+)", r.get("path", ""))
    if not m or not u:
        return None
    mod = m.group(1)
    p, c = u.get("promptTokenCount", 0), u.get("cachedContentTokenCount", 0)
    o = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    if mod not in PRICE:
        return mod, p, o, None
    i, ci, oi = PRICE[mod]
    return mod, p, o, (p - c) / 1e6 * i + c / 1e6 * ci + o / 1e6 * oi


def cap_line(today):
    cap = read_cap()
    spent = sum(v[3] for v in today.values())
    label = LABEL_FILE.read_text().strip() if LABEL_FILE.exists() else ""
    bits = [f"today ${spent:.2f}" + (f" of a ${cap:.2f} daily cap" if cap is not None else " (no daily cap)")]
    if label:
        bits.append(f"labelling calls as <b>{label}</b>")
    return f"<p class=dim>{' &middot; '.join(bits)}</p>"


def dashboard_html(rows):
    day = time.strftime("%Y-%m-%d")
    tot, today, last = {}, {}, []
    for r in rows:
        cc = call_cost(r)
        if not cc:
            continue
        mod, p, o, usd = cc
        for d, keep in ((tot, True), (today, time.strftime("%Y-%m-%d", time.localtime(r["t"])) == day)):
            if keep:
                t = d.setdefault(mod, [0, 0, 0, 0.0, mod in PRICE]); t[0] += 1; t[1] += p; t[2] += o; t[3] += usd or 0
        last.append((r, mod, p, o, usd))
    def cards(d, title):
        if not d:
            return f"<section><h2>{title}</h2><p class=dim>No calls yet.</p></section>"
        out = "".join(f"<div class=card><b>{m.replace('gemini-', 'Gemini ').replace('-', ' ').title()}</b><big>{f'${v[3]:.2f}' if v[4] else 'no price set'}</big>"
                      f"<span>{v[0]} calls &middot; {v[1]:,} in &middot; {v[2]:,} out</span></div>" for m, v in d.items())
        return f"<section><h2>{title}</h2><div class=row>{out}</div></section>"
    trs = "".join(f"<tr><td>{time.strftime('%H:%M:%S', time.localtime(r['t']))}</td><td>{r.get('label', '')}</td><td>{m.replace('gemini-', '')}</td>"
                  f"<td>{(r.get('thinkingLevel') or '').upper()}</td><td>{r['seconds']:.1f} s</td><td>{p:,}</td><td>{o:,}</td>"
                  f"<td>{f'${usd:.4f}' if usd is not None else 'n/a'}</td><td class={'ok' if r['status'] == 200 else 'bad'}>{r['status']}</td></tr>"
                  for r, m, p, o, usd in reversed(last[-25:]))
    return f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5><title>agy-gemini4</title>
<style>body{{margin:0;padding:32px 40px;background:#0b0f1a;color:#e6ebf5;font:15px/1.4 -apple-system,system-ui,sans-serif}}
h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:14px;letter-spacing:.08em;text-transform:uppercase;color:#9aa4bf;margin:28px 0 10px}}
.dim{{color:#9aa4bf}}.row{{display:flex;gap:16px;flex-wrap:wrap}}.card{{background:#141a2e;border:1px solid #26304d;border-radius:14px;
padding:16px 20px;min-width:240px;display:flex;flex-direction:column;gap:4px}}.card big{{font-size:34px;font-weight:700;color:#7cc4ff}}
.card span{{color:#9aa4bf;font-size:13px}}table{{border-collapse:collapse;width:100%;font:13px ui-monospace,Menlo,monospace}}
td,th{{padding:6px 10px;border-bottom:1px solid #1e2640;text-align:left}}th{{color:#9aa4bf;font-weight:600}}.ok{{color:#22c55e}}.bad{{color:#ff5a7a}}</style>
<h1>Gemini in Antigravity</h1><div class=dim>agy-gemini4 relay &middot; list prices from aistudio.google.com/docs/pricing &middot; refreshes every 5 s</div>
{cap_line(today)}{cards(today, "Today")}{cards(tot, "All time")}
<section><h2>Last calls</h2><table><tr><th>time</th><th>label</th><th>model</th><th>effort</th><th>took</th><th>in</th><th>out</th><th>cost</th><th>status</th></tr>{trs}</table></section>"""


def cost(ledger):
    if not os.path.exists(ledger):
        print("No calls logged yet."); return
    tot, labels = {}, {}
    for line in open(ledger):
        r = json.loads(line)
        cc = call_cost(r)
        if cc:
            t = tot.setdefault(cc[0], [0, 0.0, cc[3] is not None]); t[0] += 1; t[1] += cc[3] or 0
            if r.get("label"):
                l = labels.setdefault((r["label"], cc[0]), [0, 0.0, cc[3] is not None]); l[0] += 1; l[1] += cc[3] or 0
    for mod, (n, usd, priced) in tot.items():
        print(f"{mod}: {n} calls, " + (f"${usd:.2f} at list price" if priced else "no price set in relay.py"))
    cap = read_cap()
    print(f"today: ${today_spend(ledger):.2f}" + (f" of a ${cap:.2f} daily cap" if cap is not None else " (no daily cap)"))
    if labels:
        print("by label:")
        for (lab, mod), (n, usd, priced) in labels.items():
            print(f"  {lab} | {mod}: {n} calls, " + (f"${usd:.2f}" if priced else "no price set"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8918)
    ap.add_argument("--ledger", default=str(Path.home() / ".agy-gemini4" / "usage.jsonl"))
    ap.add_argument("--cost", action="store_true", help="print what the logged calls cost, then exit")
    ap.add_argument("--cap", help="daily spend cap in dollars (list price), or 'off'")
    ap.add_argument("--label", help="label every call from now on (a run name), or 'off'")
    a = ap.parse_args()
    if a.cap or a.label:
        CAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        for val, f, what in ((a.cap, CAP_FILE, "daily cap"), (a.label, LABEL_FILE, "label")):
            if val is None:
                continue
            if val.lower() == "off":
                f.unlink(missing_ok=True); print(f"{what} removed")
            else:
                if f is CAP_FILE:
                    float(val)
                f.write_text(val + "\n"); print(f"{what} set: {val}")
        raise SystemExit
    if a.cost:
        cost(a.ledger)
        raise SystemExit
    os.makedirs(os.path.dirname(a.ledger), exist_ok=True)
    if sys.stderr is None:   # started windowless (pythonw.exe, the Windows logon task): log next to the usage file
        sys.stdout = sys.stderr = open(os.path.join(os.path.dirname(a.ledger), "relay.log"), "a", buffering=1)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Relay)
    srv.ledger = a.ledger
    print(f"agy-gemini4 relay on http://127.0.0.1:{a.port} -> {UPSTREAM}", flush=True)
    srv.serve_forever()
