"""S0 smoke: parse one PDF with NDI and record the response shape. Reads NACE_API_KEY from the environment; prints no keys."""
import json, sys
from nace_sdk import NaceClient

path = sys.argv[1]
with NaceClient() as client:
    f = client.documents.upload(path)
    job = client.documents.parse(f.as_source(), output={"formats": ["markdown"]})
    done = client.jobs.wait(job.job_id)
    res = done.result if hasattr(done, "result") else done
    doc = res["document"] if isinstance(res, dict) and "document" in res else res
    md = doc.get("markdown", "") if isinstance(doc, dict) else ""
    print("PASS" if md else "FAIL", "markdown chars:", len(md))
    print("keys:", list(doc.keys()) if isinstance(doc, dict) else type(doc))
    blocks = doc.get("blocks") if isinstance(doc, dict) else None
    if blocks:
        print("block sample:", json.dumps(blocks[0])[:400])
