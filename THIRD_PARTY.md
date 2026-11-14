# Third-party software and services

Reach's own code is MIT (see `LICENSE`). Nothing below is vendored into this repository; each is installed or cloned by the setup steps.

| Component | Source | Licence | How Reach uses it |
| --- | --- | --- | --- |
| Intern (`intern-agent` v0.30.1) | github.com/nace-ai/intern | Apache-2.0 | The agent harness. Every Reach task runs in an Intern Session; Reach adds tools and a policy hook through Intern's extension interface. |
| Drex Max (`drex-max-v1.1`) | Nace console API | Nace terms | System 1: supervises and judges Intern's steps and tool calls; routes the 12 voice/typed commands. |
| NDI via `nace-sdk` | Nace console API | Nace terms | Document parsing and quote grounding for evidence cards. |
| Nace browser harness | github.com/nace-ai/drex-browser-harness-pub at commit `9f09a8c` | MIT | Browser execution. Reach applies two patches, kept in `patches/` and applied by `scripts/setup-browser.sh`: (1) `browser-harness-deepinfra.patch` adds DeepInfra as a helper-model provider (22 lines); (2) `browser-harness-drex-alias.patch` accepts the dated model id `drex-max-v1.1` where the harness expected `drex-max` (one condition each in `src/drex.ts` and `src/keycheck.ts`). |
| Qwen3.8-27B on DeepInfra | deepinfra.com | DeepInfra terms | System 2: planning and answers. |
| Qwen3-VL-235B-A22B-Instruct on DeepInfra | deepinfra.com | DeepInfra terms | Reads screen regions (`see_region`) and is the browser harness's helper model. |
| MediaPipe (`mediapipe` 1.2.2, hand landmarker model) | github.com/google-ai-edge/mediapipe | Apache-2.0 | Hand tracking for the experimental pinch worker in `perception/`. The model file is downloaded at setup, not committed. |
| Apple frameworks (Speech, Vision, AppKit) | macOS | Apple terms | On-device voice transcription, on-screen text reading, and the overlay. |

DeepInfra is a hackathon partner tool. Adobe and Keenable are not used.
Test documents (public arXiv papers) are fetched at test time and are not committed.
No credentials are stored in this repository; see `.env.example`.
