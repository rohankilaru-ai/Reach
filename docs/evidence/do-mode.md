# Do mode (EXPERIMENTAL, opt-in)

Reach can carry out a few UI actions in other Mac apps for the user. It is off by default. Turn it on with
`REACH_DO_MODE=1` in the backend's environment and install the bindings with `cd backend && uv sync --extra do`.

## Tools
| Tool | Effects | What it does |
| --- | --- | --- |
| `ax_describe(app?, query?)` | read | Lists the buttons, checkboxes, radio buttons, menu items, links and text fields of the frontmost app's focused window (role, label, enabled, index), at most 80. With `query`, it filters and also searches the app's menu bar items (e.g. `Format > Make Plain Text`). Password fields are never listed. |
| `ax_press(app, element_ref)` | write | `AXPress` on one listed button, checkbox, menu item, tab or link, then reads back: verified when the element's value/title or the window (title, frame, window count, element count) changed, else unverified. |
| `ax_set_text(app, element_ref, text)` | write | Sets `AXValue` of one listed text field or text area, then reads it back: verified only when it holds exactly the text. |
| `finder_list(path)` | read | Names in a folder inside the home folder, at most 100. Always available. |
| `finder_open(path)` | write | Opens a folder in Finder or a document in its default app with `/usr/bin/open`. Folders are verified by reading Finder's front window target. |
| `finder_reveal(path)` | write | `open -R`: shows the item selected in Finder. |

`ax_*`, `finder_open` and `finder_reveal` are registered only when `REACH_DO_MODE=1`. Their `write` effect means
Intern's guard and the Drex judge see every call. Each result's first line is `outcome=verified|unverified|failed`,
which feeds the receipt's `write_outcome` (a failed or unverified action is never shown as done). Each action is logged
to the Drex log as type `action`.

## Safety
- Only the macOS Accessibility API's semantic calls: `AXPress` and setting `AXValue`. No keystrokes, no synthesized
  mouse events, no System Events, no drag and no scroll.
- Accessibility trust is checked with the prompt option off. Without it, the tools return
  `needs you: allow Accessibility for <app> in System Settings > Privacy & Security > Accessibility`, where `<app>` is
  the app Reach runs under (found by walking up the process tree).
- Before each action the overlay ghost pointer moves to the element's centre with `About to press: <title>` (or
  `About to type into: ...`) for 1.0 s. Then the element is resolved again; a different frontmost app, window title,
  element role or element title is refused as stale.
- Kill switch: any mouse move, click, scroll, key or modifier from the user after the action started stops it and every
  later action in the same ask (`user took over`). Esc is the overlay's abort.
- At most 8 actions per ask.
- Refused outright: System Settings, Keychain Access, Passwords, security and login agents, terminal apps; secure text
  fields; text fields labelled like passwords, PINs, card numbers or tokens; any element whose title reads like pay,
  purchase, buy, delete, erase, send money, transfer, confirm purchase, submit/place order or checkout. Those return
  `needs the owner to do this step`.
- Finder: the path must resolve (symlinks followed) inside the home folder; secret-bearing paths (the same patterns as
  `guard_policy`: `.env*`, `.ssh`, `.aws`, `.intern-home`, `.reach/run`, keychains, key files) are refused; apps,
  scripts, installers and executables are never opened.

## What it cannot do
- It does not drag, scroll, hover or press keys. It cannot type into an app that only takes keystrokes.
- Apps with poor accessibility support (some games, custom-drawn or cross-platform UIs) expose few or no elements.
- Web pages in Chrome are only visible when Chrome's accessibility tree is on (it turns on when an assistive app asks,
  or with `--force-renderer-accessibility`); Do mode does not switch it on.
- It acts only in the frontmost app; it does not bring another app to the front.
- Menu items are listed only when `query` is given, to keep the list short.

## Gate (2026-11-14, real, on this Mac)
`cd backend && uv run --extra do python ../smoke/do_mode_gate.py` on a throwaway TextEdit document it creates itself
(closed without saving):
```
accessibility trusted: True | host app: Terminal
describe: window "Untitled", 16 elements
text area: [3] AXTextArea ""
ax_set_text: outcome=verified | Read back the field: it holds exactly the text.
TextEdit's own readback matches: True
press target: [7] AXCheckBox "bold"
ax_press: outcome=verified | Pressed "bold"; its state or the window changed.
pointer shown first: [(445, 364, 'About to type into: AXTextArea'), (171, 124, 'About to press: bold')]
close without saving: ok
```
The kill switch fired for real on the first runs (someone was using the mouse), so the gate waits for 5 s without
input and retries an aborted action at most 3 times. `kCGAnyInputEventType` turned out to reset every few seconds with
nobody at the machine, so the kill switch reads only mouse, scroll, key and modifier event types.

Finder: `finder_list` and `finder_open` on a temporary `~/Reach-test-folder` returned `verified` (Finder's front window
showed the folder); the window was closed and the folder removed.

Key-free tests: `backend/tests/test_do_mode.py` (fake Accessibility layer: stale refusal, deny-listed titles and apps,
secure fields, readback verdicts, budget, user takeover, tools absent without the flag; Finder path escape, symlink,
secrets, launchable files, readback, list cap).

Not yet checked: the ghost pointer drawn by the overlay app for these messages (coordinates are global screen points
with a top-left origin, as the Accessibility API reports them), and a full voice ask through Intern with the flag on.
