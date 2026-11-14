# browser_cloud: Nace's hosted browser (Drex Max), measured 2026-11-14

One direct SDK run (`client.browser_runs.run`), start URL a public site, `max_cost_usd` 0.10, `max_seconds` 90:

| Field | Value |
| --- | --- |
| Task | "Open the page and tell me the title of the top story." on news.ycombinator.com |
| Status / outcome | `succeeded` / `completed` |
| Answer | the real top story title at that moment |
| Wall time | 15.6 s |
| Cost reported by Nace | USD 0.002825 |

Compared with the local headless runner on the same kind of task (24 to 140 s, and a CAPTCHA on Google and DuckDuckGo), the hosted browser was faster and was not blocked. One run, not a benchmark. Nace refuses banking, payment, identity, large social and government sites and the browser has no logins; Reach uses the user's own Chrome for signed-in pages.
