"""
Make privacy-cleaned copies of saved Affinda results for the app to ship with,
so reviewers can try demo mode without spending credits.

Reads:   the files listed in PUBLIC_SOURCES below (or given on the command line)
Writes:  demo_public/<name>.json   (cleaned, safe to commit; the folder is replaced each run)

What is kept: only the extracted values the scoring uses (name, education,
skills, experience, dates, coursework, raw text).
What is removed:
  * contact fields: email, phone, location, linkedinUrl, otherLinks
  * all Affinda metadata (document IDs, links to the original PDF, review URLs)
  * annotation details (page coordinates, confidence scores, etc.)
  * emails, phone numbers and web links anywhere in the text

Usage:  python make_public_demos.py
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "demo_public"
DROP_FIELDS = {"email", "phone", "location", "linkedinUrl", "otherLinks", "pageCount"}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+?\d[\d ()-]{7,}\d)")
URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|com\.au|io|net|org)(?:/\S*)?", re.IGNORECASE)


SOCIAL = re.compile(r"\b(instagram|linkedin|github|twitter|tiktok|facebook|x)\s*:\s*@?[\w.\-]+(?:\s+[A-Z][a-z]+)?",
                    re.IGNORECASE)


def scrub(text: str) -> str:
    text = SOCIAL.sub(lambda m: f"{m.group(1)}: [handle removed]", text)
    text = EMAIL.sub("[email removed]", text)
    text = URL.sub("[link removed]", text)
    # Phone numbers: long digit runs, but keep years, scores and date ranges
    return PHONE.sub(lambda m: m.group(0) if len(re.sub(r"\D", "", m.group(0))) < 9 else "[phone removed]", text)


def slim(node):
    """Keep only raw/parsed values (recursively); scrub every string."""
    if isinstance(node, list):
        return [slim(x) for x in node]
    if isinstance(node, str):
        return scrub(node)
    if not isinstance(node, dict):
        return node
    if "parsed" in node or "raw" in node:
        parsed = node.get("parsed")
        if isinstance(parsed, dict):
            # location-like values (have 'formatted') are dropped entirely
            parsed = None if "formatted" in parsed else {k: slim(v) for k, v in parsed.items()}
        else:
            parsed = slim(parsed)
        return {"raw": slim(node.get("raw")), "parsed": parsed}
    return {k: slim(v) for k, v in node.items() if k not in ("location",)}


# Only these saved scans are published with the app (fictional resumes only).
PUBLIC_SOURCES = [
    "demo_resumes/jean_michel_basquiat.json",
    "demo_resumes/bruce_wayne.json",
    "demo_resumes/anthony_edwards.json",
]


def main() -> None:
    sources = [HERE / p for p in (sys.argv[1:] or PUBLIC_SOURCES)]
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.json"):                       # start fresh so removed resumes don't linger
        old.unlink()
    for src in sources:
        if not src.exists():
            continue
        data = json.loads(src.read_text(encoding="utf-8")).get("data") or {}
        clean = {k: slim(v) for k, v in data.items() if k not in DROP_FIELDS}
        name = (clean.get("fullName") or {}).get("parsed") or src.stem
        slug = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
        out = OUT / f"{slug}.json"
        out.write_text(json.dumps({"data": clean}, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{src.name} -> {out.relative_to(HERE)}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
