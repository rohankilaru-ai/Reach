# Running the Nace browser agent in your own Chrome

The Nace browser harness ships a Chrome extension (the "Drex Browser Agent" side panel). With
`patches/browser-harness-extension-deepinfra.patch` it accepts a DeepInfra key, so it can run with the same
helper model Reach uses (Qwen3-VL-235B-A22B-Instruct on DeepInfra) inside your own, signed-in Chrome.

## Build it

`scripts/setup-browser.sh` applies the three patches and, as its last step, builds the extension in Loop mode
(the harness README calls Loop mode the more reliable one). To rebuild by hand:

```
cd vendor/drex-browser-harness
DREX_MODE=loop npm run build
```

The unpacked extension is written to `vendor/drex-browser-harness/dist/`.

## Load it (you do this, once)

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose
   `/Users/rohankilaru/Rohan/Reach/vendor/drex-browser-harness/dist`.
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

## Letting Reach drive it (planned, not done)

The side panel has a **Let coding agents use Drex** setting. Turning it on pairs the extension with a local MCP
server (`vendor/drex-browser-harness/mcp/server.mjs`) using a pairing code the panel shows, so an agent on this Mac
can hand tasks to the extension and watch them run in your signed-in Chrome. Reach does not use this yet: today Reach
runs the harness through its own runner. Wiring Reach to the paired extension is planned, not built.
