# agy-gemini4

**Gemini 4 Carbon and Gemini 4 Argon in Google Antigravity, on your own Gemini API key.**
The `agy` CLI and the Antigravity desktop app, one command, with a live dashboard of what every call costs.

```sh
git clone https://github.com/AnassKartit/agy-gemini4 && cd agy-gemini4 && ./setup.sh
```

Then open a new terminal, run `agy`, type `/model` and pick **gemini-4-carbon**. Quit and reopen the desktop app and
pick Carbon in its model menu. Open **http://127.0.0.1:8918** for the dashboard.

## Why this exists

On an API key, Antigravity only lists older Gemini models. Its gateway mode (`AGY_LLM_GATEWAY_URL` +
`AGY_LLM_GATEWAY_MODELS`) can offer Gemini 4, but two things break:

| What Antigravity sends | What the Gemini API says | What the relay does |
|---|---|---|
| model `gemini-4-carbon-high` (effort glued to the name) | 404, no such model | calls `gemini-4-carbon` and puts `high` into `thinkingConfig.thinkingLevel` |
| the desktop app sends your key as `Authorization: Bearer ...` | 401, Bearer is for OAuth | sends it as `x-goog-api-key` |
| agy side calls with an 8 s deadline | 400, minimum is 10 s | raises it to 10 s |

`relay.py` is one Python file with no dependencies. It listens on `127.0.0.1` only, forwards everything else unchanged to
`generativelanguage.googleapis.com`, and writes Google's own token counts for each call to `~/.agy-gemini4/usage.jsonl`.

```
agy / Antigravity app  ->  relay.py (127.0.0.1:8918)  ->  Gemini API (gemini-4-carbon, gemini-4-argon)
                              |
                              +-> usage.jsonl -> dashboard at http://127.0.0.1:8918
```

## What setup.sh does (macOS)

1. Asks for your Gemini API key once ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)) and stores it
   in the macOS Keychain (item `agy-gemini4`). It is never written into a settings file; the apps get the placeholder
   `relay`.
2. Asks Google which Gemini 4 models your key can use and prints them.
3. Starts the relay at login (LaunchAgent `net.agy-gemini4.relay`).
4. Gives the desktop app the gateway settings at login (LaunchAgent `net.agy-gemini4.env`).
5. Sets `"modelProvider": "gemini"` in `~/.gemini/antigravity-cli/settings.json` (old file kept as `.bak`) and adds an
   `agy` function to `~/.zshrc`.

`./setup.sh --remove` undoes all of it, including the Keychain item.

## What it costs

Gemini 4 Carbon and Argon are paid-tier only. List prices per 1M tokens
([aistudio.google.com/docs/pricing](https://aistudio.google.com/docs/pricing)):

| | Input | Cached input | Output (incl. thinking) |
|---|---|---|---|
| until 2026-11-30 | $2.00 | $0.10 | $10.00 |
| from 2026-12-01 | $4.00 | $0.20 | $20.00 |

Measured: a playable one-file snake game built by agy on Carbon took 3 calls and cost **$0.12**. An agent session
re-sends its whole context on every step, so long sessions add up; most of it is billed at the cached price.
`python3 relay.py --cost` prints your total from the usage file. Prices in `relay.py` (`PRICE`) need an update after
2026-11-30.

## Effort

agy has an effort picker (low, medium, high). The desktop app always asks for high on gateway models; write
`low`, `medium` or `high` into `~/.agy-gemini4/thinking` to override it for Gemini 4 calls (delete the file to stop).

## Troubleshooting

- **No Gemini 4 model on this key:** turn on billing for the key's project in AI Studio.
- **`agy` still shows old models:** open a new terminal (the `agy` function is in `~/.zshrc`).
- **Desktop app still shows old models:** quit it fully (Cmd+Q) and reopen it.
- **Relay log:** `~/.agy-gemini4/relay.log`. Rejected requests (400) are saved to `~/.agy-gemini4/rejected/` without
  your key.

Tested on macOS with Antigravity CLI 1.5.5. Not affiliated with Google. MIT licence.

Made by [Anass Kartit](https://www.kartit.net).
