# Reach silent speech (S8a): EXPERIMENTAL, research weights

> **Licence warning.** The lip-reading checkpoint (`vsr_trlrs2lrs3vox2avsp_base.pth`, Auto-AVSR) was trained on LRS2,
> LRS3, VoxCeleb2 and AVSpeech, which are licensed for **research and non-commercial use only**. `setup.sh` downloads
> it into the gitignored `models/` folder; it is never committed. Do not ship it in a product. See `THIRD_PARTY.md`.

Mouth a command at the camera; the worker lip-reads one short clip, sends N-best transcript hypotheses to the Reach
backend, and the backend's 12-command Drex router picks a command or says `uncertain`. `uncertain` never acts.

Its own uv project and venv. Nothing here is added to `backend/` or to `perception/`'s venv.

## Setup
```
perception/silent/setup.sh            # ~40 s on a fast link; --no-model skips the 1 GB checkpoint
```
It runs `uv sync --extra model` (torch 2.15.0, torchvision 0.29.2, mediapipe 1.2.2, opencv; pins from
[silent-chat](https://github.com/Input-Labs-HQ/silent-chat)'s `requirements.txt`), clones Auto-AVSR at commit
`182b628` and applies `patches/auto_avsr-ctc-device.patch` (CTC scoring on the input's device, for MPS), then checks
each download URL with a HEAD request before fetching the checkpoint (Google Drive id from silent-chat's
`setup.command`, via `gdown`) and the MediaPipe face detector. Measured sizes: `.venv` ~1 GB, `third_party/auto_avsr`
87 MB, checkpoint 955 MB, face detector 228 KB.

## Run
```
cd perception/silent
uv run python -m reach_silent.worker --hyp "explain this" "explain miss"   # typed hypotheses -> backend; no camera
uv run --extra model python -m reach_silent.worker --selftest             # SYNTHETIC crops: shapes/timing only
uv run --extra model python -m reach_silent.worker --camera-check         # permission status; 3 s probe if granted
uv run --extra model python -m reach_silent.worker --on                   # turn it on: preview + push-to-mouth
uv run pytest -q                                                          # key-free, camera-free, model-free
```
- **Off by default.** No flag means nothing happens and the camera stays closed. `--on` opens a preview window
  labelled EXPERIMENTAL. Tap SPACE in that window to start a clip, tap again to stop (4 s max). Nothing is always
  listening. The preview shows the top hypothesis, the chosen command and its probability, or "uncertain: did nothing".
- **Backend:** start `reach serve` first. The worker reads `$REACH_DIR/run/server.json` and `run/token` (default
  `<repo>/.reach`; set `REACH_DIR` when running from a worktree). With no backend, the worker prints the message and
  "not routed": it never acts on its own guess.

## Pipeline
1. Camera frames (BGR, in memory) while the clip key is on, resampled to 25 fps.
2. MediaPipe face detector -> 4 keypoints per frame -> Auto-AVSR `VideoProcess` -> 96x96 mouth crops (`mouth.py`).
3. Auto-AVSR conformer: encoder on MPS, beam search (beam 20) on CPU (`lipreader.py`). Two outputs per clip:
   the open-vocabulary 5-best `(text, log_prob)`, and closed-set log-probs of the 11 command phrases.
4. `commands.py`: the open n-best is re-ordered (hypotheses close to a command phrase first, the closed-set winner
   first among those) and sent as
   `{"type": "transcript", "hypotheses": [...], "scores": [...], "source": "lipreading", "experimental": true}`.
   Only the open n-best is sent, so unrelated mouthing can still come back `uncertain`. Sending the closed-set phrases
   would force every clip onto some command.
5. Backend (`backend/reach/ipc.py`, additive): routes the hypotheses with `commands.route` (one Drex Max `choice`,
   logged as purpose `command_route`) and broadcasts
   `{"type": "transcript", "role": "user", "text", "command", "probability", "uncertain", "reason", "source", "experimental"}`
   to the sender and relays it to overlay clients (hello role `overlay`). The overlay only acts on assistant transcripts today, so this result does not drive any
   action yet.

## Status (2026-11-14): what ran, what did not
**Ran for real on this Mac (Apple Silicon, 16 GB):**
- `setup.sh`: full install, the checkpoint download and the patch, in 41 s.
- The real model on **synthetic** random-pixel crops (`--selftest`). This proves shapes, load, memory and latency.
  It is **NOT an accuracy test**: random pixels contain no words.
  - Load: 20.7 s cold (first run after install), 2.8 s warm (files cached). Peak RSS ~2.2 GB while loading.
  - Per clip, hybrid device, beam 20: the first clip is slow because of MPS warm-up (1.3 s for 1.5 s of video).
    After that, 0.44 s for a 1.5 s clip (encode 45 ms, beam 376 ms, closed set 20 ms) and 1.3 s for a 3 s clip.
- The typed path end to end against a live `reach serve`, with real Drex Max calls (~0.3 to 0.5 s each, logged as
  `command_route`):
  - `"explain this" "explain miss"` -> `explain_this` p=0.98.
  - `"we're going to talk about"` -> `uncertain` (did nothing).
  - `"scroll dow" "scroll up"` -> `uncertain` (conflicting hypotheses).
- `--camera-check`: macOS reported Camera as **authorized** for the process that ran it (the coding-agent host, not
  necessarily Terminal). It read 36 frames in 3 s (12 fps at 1280x720) in memory and wrote nothing to disk.

**Not done or not measured:**
- **No accuracy number.** No real labelled clips of mouthed commands were available, and the selftest uses noise.
  The owner will try it live with `--on`.
- No recorded face video was available offline, so `mouth.py` (face detection -> crop) has not run on a real face.
- `--on` (preview, push-to-mouth, crop, read, send) has not been run interactively.
- The overlay does not consume the routed result yet.

## Camera permission
The camera opens only with `--on` or `--camera-check`, and only after
`AVCaptureDevice.authorizationStatusForMediaType` reports `authorized` for this process. Checking the status does not
open the camera. In any other state the worker prints the blocker (System Settings > Privacy & Security > Camera, for
the terminal app that runs it; quit and reopen the terminal after) and exits 2. It does not retry or wait on a prompt.
Frames stay in memory: no video or image file is ever written.

## Credits
The approach and parts of `mouth.py` / `lipreader.py` are adapted from
[Input-Labs-HQ/silent-chat](https://github.com/Input-Labs-HQ/silent-chat) (MIT). Its Claude dispatch is not used here;
Reach's own Drex router decides. See the root `THIRD_PARTY.md`.
