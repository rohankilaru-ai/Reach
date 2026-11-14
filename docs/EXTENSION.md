# Running the Nace browser agent in your own Chrome

The Nace browser harness ships a Chrome extension (the "Drex Browser Agent" side panel). With
`patches/browser-harness-extension-deepinfra.patch` it accepts a DeepInfra key, so it can run with the same
helper model Reach uses (Qwen3-VL-235B-A22B-Instruct on DeepInfra) inside your own, signed-in Chrome.

## Build it

`scripts/setup-browser.sh` applies the three patches. Then build the extension in Loop mode (the harness README
calls Loop mode the more reliable one):

```
cd vendor/drex-browser-harness
DREX_MODE=loop npm run build
```

The unpacked extension is written to `vendor/drex-browser-harness/dist/`.

## Load it (you do this, once)

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose
   `vendor/drex-browser-harness/dist` inside your clone of this repository.
4. Open the side panel with **Alt+Shift+D** (or click the Drex icon in the toolbar).
5. Click the **gear icon** to open settings and enter:
   - **Drex API key**: your Nace (Drex) key.
   - **LLM API key**: your DeepInfra key (it starts with `sk-di-`; the panel labels it "DeepInfra key").
6. In **Model for planning and help**, pick **Qwen3 VL 235B (DeepInfra)**.
7. Optional: **Check keys** makes one tiny request to Drex and one to DeepInfra to confirm both keys work.
8. Click **Save**, type a task in plain words, and run it.

While it works, Chrome shows a bar saying the extension "started debugging this browser". That is the
extension driving the tab through Chrome's debugger. **Stop** in the side panel ends the run and the bar goes away.

Keys stay in the extension's own `chrome.storage.local` and are sent only to their own provider. After pulling a
new build, click the reload arrow on the extension's card in `chrome://extensions`; your saved settings stay.

## Which key goes where

- **In the extension's settings** (Chrome, gear icon): the Nace (Drex) key and the DeepInfra key. The extension keeps its
  own copies; it does not read Reach's files.
- **In the repo-root `.env`** (names in `.env.example`): `NACE_API_KEY` and `DEEPINFRA_API_KEY` for Reach itself.
- **In `.reach/server.env`**: `REACH_PAIRING_CODE`, only if you want Reach to drive the extension (next section). It is a
  pairing code, not an API key.

## Let Reach use it (pairing, experimental)

Reach can hand an ask to this extension so it runs in your own, signed-in Chrome (YouTube, Gmail, any site you use).
It talks to the extension through the harness's local pairing server (`mcp/server.mjs`, on `127.0.0.1:47615`), which
Reach starts itself when it needs it. Untested with a real extension so far; only a fake pairing server is tested.

1. In Chrome, open the Drex side panel (**Alt+Shift+D**) and click the **gear icon**.
2. Turn on **Let coding agents use Drex**. It shows a pairing code.
3. Add one line to `.reach/server.env` in the repo: `REACH_PAIRING_CODE=<the code it shows>`.
4. Restart Reach.

Reach then gets the `browser_in_chrome` tool. Only an extension status of `done` counts as done; partial, needs-you,
failed, stopped, a timeout (120 s by default, `REACH_CHROME_TIMEOUT_S`) or an extension that is not connected are
reported as not done, with the reason. Abort closes the link to Chrome (the run in Chrome may still finish its current
move). A risky step (pay, send, delete) always ends the run and hands control back to you. The pairing code is never
logged or shown in results. The pairing server sends no progress updates, so Reach shows none while the run goes.
If you click **New code** in the extension, update `.reach/server.env` and restart Reach.
