# NDI ground: real response shape (recorded 2026-11-14)

Call (nace-sdk 0.8.0 has it; no raw HTTP needed):

```python
job = client.documents.ground({"type": "parse_result", "job_id": "<parse job id>"},
                              targets=[{"id": "t1", "text": "<quote>"}],
                              options={"max_matches": 3, "include_previews": True})
done = client.jobs.wait(job.job_id)   # ground is an async job like parse; done.result is below
```

Note: SDK keyword arguments become top-level body fields, so the options must go in `options={...}`.

Raw `done.result` from the first live call (quote from the Transformer paper; ids and the signed crop URL redacted):

```json
{
  "result_type": "ground",
  "targets": [
    {
      "id": "t1",
      "status": "found",
      "matches": [
        {
          "rank": 1,
          "matched_text": "The Transformer allows for significantly more parallelization",
          "match_method": "exact",
          "confidence": 1,
          "location": {
            "kind": "page_region",
            "page": 2,
            "polygons": [[[0.17597, 0.40758], [0.57457, 0.40758], [0.57457, 0.42016], [0.17597, 0.42016]]]
          },
          "cropped_image_url": "https://console.nace.ai/v1/documents/jobs/<job>/ground-crops/<redacted>"
        }
      ],
      "error": null
    }
  ],
  "units": 5,
  "transcript_url": null,
  "converted_pdf_url": null
}
```

- `page` is 1-based. `polygons` are page-normalized (0..1) corner lists, one polygon per line the match spans.
- `cropped_image_url` (with `include_previews`) is an authenticated API URL to a PNG crop of the match; Reach does not
  pass it to clients.
- **Citation level supported: 3** (quote + passage id, page, and a page-region bbox). Reach builds each card from the passage store
  (level 1, or 2 when the passage page is known), runs one ground job per source document for all quotes of the
  answer, and attaches the best match as `locator` (level 3). If ground fails or times out the card still ships.
- Parse note: the parse result for PDFs had `chunks: []` and `blocks: null`; the Markdown carries `--- Page N ---`
  lines, which is where passage page numbers come from.
