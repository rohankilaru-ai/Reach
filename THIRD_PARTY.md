# Third-party software and services

| Component | Source | Licence | How Reach uses it |
| --- | --- | --- | --- |
| Intern (`intern-agent` v0.30.1) | github.com/nace-ai/intern | Apache-2.0 | The agent harness. Every Reach task runs in an Intern Session. Installed from the pinned tag, not vendored. |
| Drex Max (`drex-max-v1.1`) | Nace console API | Nace terms | System 1: supervises and judges Intern's steps and tool calls. |
| NDI via `nace-sdk` | Nace console API | Nace terms | Document parsing and quote grounding for evidence cards. |
| Nace browser harness | github.com/nace-ai/drex-browser-harness-pub, base commit `9f09a8c` | MIT | Browser execution. Reach adds one patch (`patches/browser-harness-deepinfra.patch`, 22 lines) that adds DeepInfra as a helper-model provider. The harness is cloned by `scripts/setup-browser.sh`, never vendored. |
| Qwen3.8-27B on DeepInfra | deepinfra.com | DeepInfra terms | System 2: planning and answers. DeepInfra is a partner tool. |
| Qwen3-VL-235B-A22B-Instruct on DeepInfra | deepinfra.com | DeepInfra terms | The browser harness's helper model (planning and reading pages). |
| MediaPipe, Auto-AVSR, silent-chat | see `perception/` if present | Apache-2.0 / MIT; lip-reading weights are research and non-commercial | Experimental only; weights are downloaded by the user and never shipped. Listed here only if that code is added. |

Test documents are public papers fetched at test time and are not committed.
Reach's own code is MIT (see `LICENSE`). No credentials are stored in this repository; see `.env.example`.
