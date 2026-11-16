"""
One-off helper: scan a resume with Affinda ONCE and save the result to
demo_resumes/<name>.json so it can be reused in demo mode for free.

Usage:  python scan_demo_resume.py "path/to/resume.pdf" name_for_file

Each run uses one Affinda credit. The result is written to disk before
anything else happens. The document is NOT deleted from Affinda, so it can
also be re-fetched for free later if the local file is ever lost.
Never prints the API key.
"""
import json
import pathlib
import sys
import time
import tomllib

import requests

HERE = pathlib.Path(__file__).parent
BASE = "https://api.affinda.com/v3"


def main(pdf_path: str, name: str) -> None:
    out_dir = HERE / "demo_resumes"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{name}.json"
    if out.exists():
        sys.exit(f"{out.name} already exists. Not scanning again (would waste a credit).")

    sec = tomllib.loads((HERE / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))
    headers = {"Authorization": f"Bearer {sec['AFFINDA_API_KEY']}", "Accept": "application/json"}
    pdf = pathlib.Path(pdf_path)
    print(f"Uploading {pdf.name} ({pdf.stat().st_size / 1e6:.1f} MB)…")
    with pdf.open("rb") as f:
        r = requests.post(f"{BASE}/documents", headers=headers,
                          files={"file": (f"{name}.pdf", f, "application/pdf")},
                          data={"workspace": sec["AFFINDA_WORKSPACE_ID"],
                                "documentType": sec["AFFINDA_DOCUMENT_TYPE_ID"],
                                "wait": "true"},
                          timeout=300)
    print("Upload status:", r.status_code)
    if not r.ok:
        sys.exit("Upload failed: " + r.text[:500])
    doc = r.json()
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")   # save immediately
    doc_id = doc["meta"]["identifier"]
    print("Document identifier:", doc_id)

    deadline = time.time() + 300
    while not doc["meta"].get("ready") and not doc["meta"].get("failed") and time.time() < deadline:
        time.sleep(4)
        g = requests.get(f"{BASE}/documents/{doc_id}", headers=headers, params={"format": "json"}, timeout=30)
        if g.ok:
            doc = g.json()
            out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print("ready:", doc["meta"].get("ready"), "| failed:", doc["meta"].get("failed"))

    saved = json.loads(out.read_text(encoding="utf-8"))
    fields = [k for k, v in (saved.get("data") or {}).items() if v]
    print(f"Saved {out.relative_to(HERE)} with {len(fields)} non-empty fields: {fields}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
