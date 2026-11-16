"""
Print a full scoring breakdown for one resume against one job ad, so you can
see exactly where a score comes from. Uses saved data only (no Affinda credits).

Usage:
  python diagnose.py <resume.json> <job ad URL or .txt file> [edu skills exp]
Example:
  python diagnose.py sample_response.json https://example.com/job 30 40 30
"""
import json
import sys
from pathlib import Path

import app


def main() -> None:
    resume_path, job = sys.argv[1], sys.argv[2]
    w = [int(x) for x in sys.argv[3:6]] if len(sys.argv) >= 6 else [30, 40, 30]
    weights = dict(zip(app.AREAS, w))

    if job.startswith("http"):
        text, title = app.fetch_job_text(job)
    else:
        text, title = Path(job).read_text(encoding="utf-8"), job

    print(f"JOB AD: {title}")
    print("\nLINES KEPT AFTER REMOVING BOILERPLATE:")
    for line, in_req in app.clean_job_lines(text):
        print(f"  {'[req] ' if in_req else '      '}{line[:110]}")

    found = app.extract_requirements(text)
    reqs = {a: app.parse_requirement_box("\n".join(found[a])) for a in app.AREAS}
    n = sum(len(v) for v in reqs.values())
    print(f"\nKEYWORDS EXTRACTED ({n}, cap {app.MAX_KEYWORDS}):")
    for a in app.AREAS:
        print(f"  {a}: {found[a]}")

    resume = app.parse_resume(json.loads(Path(resume_path).read_text(encoding="utf-8")))
    s = app.score_applicant(resume, reqs, weights)
    print(f"\nRESUME: {resume['name']}   weights {weights}")
    for a in app.AREAS:
        r = s["areas"][a]
        if r["pct"] is None:
            print(f"\n  {a}: no requirements -> left out, weight shared with other areas")
            continue
        eff = r["effective_weight"]
        print(f"\n  {a}: raw {r['pct']:.0f}%  x  weight {eff:.0f}%  =  {r['pct'] * eff / 100:.1f} points")
        for it in r["items"]:
            print(f"     {it['credit']:.2f} x{it['weight']:<3} {it['how']:<8} {it['text']:<40} {it['evidence']}")
    print(f"\n  TOTAL = {s['total']:.1f}%")


if __name__ == "__main__":
    main()
