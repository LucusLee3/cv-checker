"""
CV Check: applicant screening for HR teams.

Flow
1. HR sets how much Education, Skills and Experience matter (weights, total 100%).
2. HR pastes a job-ad URL and clicks "Scan job ad". The page is read and the
   Education / Skills / Experience requirement tabs are filled in automatically.
3. HR reviews and edits the requirements.
4. HR uploads an applicant's resume (PDF). It is sent to Affinda (document type
   "Student CV"), the extracted fields are read, and the document is deleted
   from Affinda straight away.
5. Each area scores 0-100% (share of requirements the resume meets). The final
   match = sum(area score x area weight), out of 100.
6. Applicants are listed at the bottom, best match first. Scores update live
   if HR changes the weights or requirements.

Nothing is written to disk. Applicant results live only in this browser
session's memory and disappear when the tab is closed. The API key is never
printed or logged.

Run locally:   streamlit run app.py
Demo mode:     uses ./sample_response.json so you can test without spending
               Affinda credits (the toggle only appears if that file exists).
"""

from __future__ import annotations

import difflib
import html
import ipaddress
import json
import re
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# =============================================================================
# 1. CONFIGURATION: tune everything here
# =============================================================================

APP_TITLE = "CV Checker"
MAX_CHECKS_PER_SESSION = 3          # live Affinda resume scans per browser session (credit saver)
MAX_FILE_MB = 5

AFFINDA_BASE_URL = "https://api.affinda.com/v3"
AFFINDA_TIMEOUT_S = 120

APP_DIR = Path(__file__).parent
SAMPLE_RESPONSE = APP_DIR / "sample_response.json"
DEMO_DIR = APP_DIR / "demo_resumes"                 # full saved scans from scan_demo_resume.py (git-ignored)
PUBLIC_DEMO_DIR = APP_DIR / "demo_public"           # cleaned copies from make_public_demos.py (committed)

# Example job ad shown when someone first opens the app. A saved copy of its
# text is used to pre-fill the requirements, so it still works if the live
# page is taken down.
DEFAULT_JOB_URL = "https://affindagroup.applytojob.com/apply/E1eAsBdTPg/Affinda-Internship-Program-Jan-Feb-2027"
DEFAULT_JOB_TITLE = "Affinda Internship Program Jan – Feb 2027"
DEFAULT_JOB_TEXT = APP_DIR / "default_job_ad.txt"

AREAS = ["Education", "Skills", "Experience"]
DEFAULT_WEIGHTS = {"Education": 30, "Skills": 40, "Experience": 30}

# --- Requirement library -------------------------------------------------------
# Used in two ways:
#   * Scanning a job ad: any term whose synonyms appear in the ad is added
#     to that area's requirements (using the name on the left).
#   * Matching a resume: a requirement with a name listed here matches if ANY
#     of its synonyms appears in the resume. Requirements HR types that are not
#     in the library are matched word-by-word instead.
# Synonyms are case-insensitive whole words, except single letters (e.g. "R"),
# which must match exactly.
REQUIREMENT_LIBRARY = {
    "Education": {
        "Finance": ["finance", "financial"],
        "Accounting": ["accounting", "accountancy"],
        "Economics": ["economics", "econometrics"],
        "Commerce": ["commerce"],
        "Business": ["business administration", "business studies", "business degree", "mba"],
        "Mathematics": ["mathematics", "maths", "math", "mathematical"],
        "Statistics": ["statistics", "statistical", "probability", "stochastic"],
        "Actuarial Studies": ["actuarial"],
        "Computer Science": ["computer science", "software engineering", "computer engineering"],
        "Engineering": ["engineering"],
        "Data Science": ["data science"],
        "Information Systems": ["information systems", "information technology"],
        "Law": ["law", "laws", "juris doctor", "legal studies"],
        "Physics": ["physics"],
        "Architecture": ["architecture", "architectural"],
        "Design": ["design"],
        "Marketing": ["marketing"],
        "Psychology": ["psychology"],
        "Quantitative discipline": ["quantitative"],
        "Science": ["science"],
        # Special requirements, checked against graduation dates / degrees (see SPECIAL_REQUIREMENTS)
        "Final-year student / recent graduate": [],
        "Any degree": [],
    },
    "Skills": {
        "Excel": ["excel", "spreadsheet", "spreadsheets"],
        "PowerPoint": ["powerpoint", "power point"],
        "Python": ["python"],
        "R": ["R"],
        "SQL": ["sql", "mysql", "postgresql", "t-sql"],
        "VBA": ["vba", "macros"],
        "Tableau": ["tableau"],
        "Power BI": ["power bi", "powerbi"],
        "Java": ["java"],
        "JavaScript": ["javascript", "typescript"],
        "C++": ["c++"],
        "MATLAB": ["matlab"],
        "Bloomberg": ["bloomberg"],
        "Capital IQ": ["capital iq", "capiq"],
        "FactSet": ["factset"],
        "Git": ["git", "github"],
        "AWS": ["aws", "amazon web services"],
        "Financial modelling": ["financial modelling", "financial modeling", "financial model", "financial models",
                                "dcf", "three-statement", "3-statement"],
        "Valuation": ["valuation", "valuations"],
        "Data analysis": ["data analysis", "data analytics", "analysing data", "analyzing data"],
        "Machine learning": ["machine learning", "artificial intelligence"],
        "Communication": ["communication", "communications", "communicate"],
        "Presentation": ["presentation", "presentations", "presenting"],
        "Stakeholder management": ["stakeholder", "stakeholders"],
        "Problem solving": ["problem solving", "problem-solving", "solve problems", "solving problems"],
        "Attention to detail": ["attention to detail", "detail-oriented", "detail oriented"],
        "Teamwork": ["teamwork", "team player", "collaboration", "collaborative"],
        "Project management": ["project management", "project manager", "managing projects"],
        "Research": ["research", "researching"],
        "AI": ["ai", "artificial intelligence", "generative ai", "genai", "llm", "llms", "chatgpt",
               "large language model", "large language models"],
        "Coding / programming": ["coding", "coder", "vibe coding", "write code", "programming language",
                                 "programming languages", "software programming"],
        "Automation": ["automation", "automate", "automating", "automated"],
        "Product design": ["product design", "ux design", "ui design", "user experience design"],
        "Creativity": ["creative", "creativity"],
        "Adobe Creative Suite": ["adobe", "photoshop", "illustrator", "indesign"],
        "Revit": ["revit"],
        "Rhino": ["rhino"],
        "AutoCAD": ["autocad"],
    },
    "Experience": {
        "Consulting": ["consulting", "consultant", "consultants", "consultancy", "advisory"],
        "Banking": ["bank", "banks", "banking", "banker"],
        "Investment banking": ["investment banking", "investment bank"],
        "Private equity": ["private equity"],
        "Venture capital": ["venture capital"],
        "Asset management": ["asset management", "funds management", "fund manager", "portfolio management"],
        "Corporate finance / M&A": ["corporate finance", "m&a", "mergers", "acquisitions", "corporate development",
                                    "capital raise", "capital raising", "fundraising", "seed round", "due diligence"],
        "Audit": ["audit", "auditing", "assurance"],
        "Tax": ["tax", "taxation"],
        "Financial services": ["financial services", "fintech", "insurance", "superannuation", "wealth management"],
        "Client-facing": ["client", "clients", "client's", "client-facing", "customer", "customers"],
        "Sales": ["sales", "business development"],
        "Software development": ["software development", "software engineer", "software developer", "developer"],
        "Data / analytics": ["data analyst", "data analytics", "data analysis", "business intelligence"],
        "Market research": ["market research", "competitor analysis", "benchmarking"],
        "Leadership": ["led a team", "team leader", "team lead", "director", "president", "captain", "head of",
                       "supervised", "leadership"],
        "Startup": ["startup", "start-up", "saas"],
        "Product development": ["product development", "product management", "product manager", "mini product",
                                "build a product", "built a product", "mvp", "prototype", "prototyped"],
        "Events": ["events", "event"],
        "Architecture / design practice": ["architects", "architecture firm", "architectural", "design studio"],
        "Internship": ["internship", "intern", "vacationer", "summer associate"],
        "Retail / customer service": ["retail", "customer service"],
        "Not-for-profit": ["not for profit", "not-for-profit", "nfp", "charity", "social impact"],
        "Government / public sector": ["government", "public sector"],
    },
}
# Extra synonyms used only when matching resumes (not when scanning job ads),
# e.g. a Mathematics degree satisfies "Quantitative discipline".
# --- Synonym map (resume side) -------------------------------------------------
# Extra words that count as evidence for a requirement when matching resumes.
# Keys are requirement names (case-insensitive); they work whether the
# requirement came from a scan or was typed in by HR.
SYNONYMS = {
    "ai": ["chatgpt", "claude", "llm", "llms", "machine learning", "automation", "artificial intelligence",
           "generative ai", "genai", "gpt", "copilot", "prompt engineering", "deep learning", "neural network"],
    "coding / programming": ["python", "java", "javascript", "typescript", "c++", "c#", "sql", "matlab", "R",
                             "html", "css", "software development", "software developer", "github"],
    "automation": ["automated", "automate", "scripting", "vba", "macros"],
    "problem solving": ["solved", "solving", "analyse", "analysis", "analytical", "framework", "root cause",
                        "troubleshoot", "case competition", "advised", "recommendation"],
    "teamwork": ["team", "teams", "collaborated", "collaborate", "cohort", "peer", "cross-functional"],
    "creativity": ["designed", "design", "ideation", "brainstorm", "innovative", "innovation"],
    "stakeholder management": ["client", "clients", "stakeholder", "liaised", "partners", "sponsorship"],
    # note: avoid "presented"/"presenting": they normalise to "present", which matches "Feb 2025 - Present"
    "communication": ["presentation", "presentations", "wrote", "reports", "liaised", "pitch", "public speaking"],
    "presentation": ["pitch", "pitched", "deck", "decks", "public speaking"],
    "leadership": ["led", "lead", "director", "president", "captain", "head of", "supervised", "managed a team"],
    "excel": ["spreadsheet", "pivot table", "vlookup", "xlookup", "microsoft 365", "microsoft office", "ms office"],
    "powerpoint": ["microsoft 365", "microsoft office", "ms office", "keynote", "google slides"],
    "financial modelling": ["dcf", "valuation model", "three-statement", "forecast model"],
    "data analysis": ["analytics", "statistics", "statistical", "python", "R", "sql", "tableau", "power bi"],
    "research": ["researched", "market research", "benchmarking", "case studies", "analysed"],
    "quantitative discipline": ["mathematics", "maths", "statistics", "physics", "engineering", "actuarial",
                                "econometrics", "computer science", "data science", "finance", "economics"],
    "internship": ["intern"],
    "product development": ["product features", "launched", "go to market"],
    "client-facing": ["client", "clients", "customer", "customers", "customer service"],
    "startup": ["start-up", "saas", "seed round", "scale-up"],
}

# --- Job ad cleaning -----------------------------------------------------------
# Section headings: text under "skip" headings is ignored entirely; terms under
# "requirement" headings count as more important.
SKIP_SECTION_HEADINGS = [
    "who we are", "about us", "about the company", "our company", "company overview", "compensation",
    "salary", "remuneration", "benefits", "perks", "hiring process", "how to apply", "application process",
    "location", "dates", "date", "working hours", "why join us", "our values", "equal opportunity",
]
REQUIREMENT_SECTION_HEADINGS = [
    "who we're looking for", "who we are looking for", "about you", "requirements", "what you'll bring",
    "what you bring", "skills", "qualifications", "you have", "you'll need", "you will need",
    "selection criteria", "essential", "desirable", "ideal candidate", "what we're looking for",
    "what we are looking for", "key skills", "to be successful",
]
# Lines matching any of these are boilerplate and ignored (dates, pay, locations,
# addresses, application instructions, legal text).
BOILERPLATE_PATTERNS = [
    r"\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",          # 18 Jan
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(st|nd|rd|th)?,?\s+20\d\d\b",
    r"\$\s?\d", r"\b(salary|per week|per hour|per annum|p\.a\.|award rate|superannuation|remuneration)\b",
    r"\b(street|st\.|road|rd\.|avenue|level \d+|suite \d+|house,)\b", r"\b(on-site|onsite|hybrid|remote)\b",
    r"\bapply (now|online|today|here|via|through|by)\b", r"\bapplications? (close|closes|open|are)\b",
    r"\b(cover letter|hiring process|make an offer|interviews?)\b",
    r"\b(equal opportunit|inclusive workplace|unsolicited|recruitment agenc|work rights|visa|citizen|privacy)",
    r"^\s*(entry level|full[- ]time|part[- ]time|casual|contract|internship)\s*$",
]
# When scanning, education subjects only count in lines that talk about study
# (so "financial services firm" doesn't add "Finance" as a degree).
EDUCATION_CONTEXT = ["degree", "bachelor", "master", "studying", "study", "studies", "major", "discipline",
                     "qualification", "qualifications", "graduates", "grads", "university", "coursework",
                     "background in", "tertiary", "phd", "diploma", "students"]
OPTIONAL_CUES = ["preferred", "a plus", "desirable", "bonus", "advantage", "advantageous", "nice to have",
                 "considered", "ideally", "would be great"]
MUST_CUES = ["must", "required", "essential", "proficient", "great at", "strong", "demonstrated"]
# Free-text phrases after these cues are kept as requirements (e.g. "experience with Xero").
FREE_TEXT_CUES = r"(proficien\w* (?:in|with)|experience (?:in|with)|knowledge of|familiar\w* with|skilled in|" \
                 r"background in|degree in|understanding of|expertise in)"
# Words that are never useful as requirements on their own.
GENERIC_WORDS = set("""
week weeks month months year years day days team teams program programs programme apply application role
roles position opportunity opportunities company business people person student students intern interns
graduate graduates candidate candidates work working everything anything something things way ways area
areas idea ideas support value values everyday life lot part own end process solution solutions result
results level levels australia melbourne sydney global office stuff help
""".split())
MAX_KEYWORDS = 18                                    # cap on requirements filled in by a scan

# --- Matching and scoring settings ---------------------------------------------
SCORING = {
    "importance_weights": {"must-have": 2.0, "normal": 1.0, "preferred": 0.5},
    "fuzzy_credit": 0.8,             # credit for a close spelling (e.g. "arhictecture")
    "fuzzy_cutoff": 0.82,            # similarity needed for a close spelling (0-1)
    "fuzzy_min_length": 6,           # only words this long or longer are fuzzy-matched
    "partial_min": 0.5,              # multi-word requirement: share of words needed for partial credit
    "final_year_months": 18,         # graduating within this many months = final year
    "recent_grad_months": 24,        # graduated within this many months = recent graduate
    "not_final_year_credit": 0.5,    # current student, but not final year
}
# Words ignored when matching free-text requirements.
FILLER_WORDS = set("""
a an and or the of in on at to for with from by as is are be any some
experience experienced exposure working work worked knowledge strong proficiency proficient skill skills
understanding background degree degrees studies study coursework years year ability able good excellent
relevant related field fields similar using use etc e g ie
""".split())


# =============================================================================
# 2. JOB AD SCANNING: fetch a URL and pull out requirements
# =============================================================================

class JobFetchError(Exception):
    """A user-friendly error from reading a job page."""


def _check_public_host(url: str) -> None:
    """Only allow http(s) URLs that resolve to public IP addresses
    (stops the server being used to reach internal addresses)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise JobFetchError("Please enter a full web address starting with https://")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise JobFetchError("That website address couldn't be found.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise JobFetchError("That address isn't a public website.")


def _html_to_text(markup: str) -> str:
    return BeautifulSoup(markup, "html.parser").get_text("\n")


def _find_job_posting(node):
    """Find a schema.org JobPosting object inside JSON-LD data."""
    if isinstance(node, list):
        for item in node:
            found = _find_job_posting(item)
            if found:
                return found
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                found = _find_job_posting(node[key])
                if found:
                    return found
    return None


def fetch_job_text(url: str) -> tuple[str, str]:
    """Download a job page and return (job description text, page title)."""
    headers = {"User-Agent": "Mozilla/5.0 (CV Check job ad reader)", "Accept": "text/html,application/xhtml+xml"}
    try:
        for _ in range(5):                              # follow redirects manually, checking each hop
            _check_public_host(url)
            resp = requests.get(url, headers=headers, timeout=15, allow_redirects=False)
            if resp.is_redirect and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            break
    except requests.RequestException:
        raise JobFetchError("We couldn't open that page. Check the link, or paste the job description instead.") from None
    if resp.status_code in (401, 403, 429, 999):
        raise JobFetchError("That site blocks automatic reading (common for LinkedIn). "
                            "Paste the job description text instead.")
    if not resp.ok:
        raise JobFetchError(f"The page returned an error (HTTP {resp.status_code}).")
    soup = BeautifulSoup(resp.text[:3_000_000], "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "") or url

    # Many job boards embed the ad as structured data (JSON-LD JobPosting): use it if present.
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            posting = _find_job_posting(json.loads(script.string or ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if posting:
            parts = [posting.get("title", "")]
            for key in ("description", "qualifications", "skills", "educationRequirements",
                        "experienceRequirements", "responsibilities"):
                val = posting.get(key)
                if isinstance(val, (dict, list)):
                    val = json.dumps(val)
                if val:
                    parts.append(_html_to_text(str(val)))
            return "\n".join(parts), posting.get("title") or title

    # Otherwise use the visible page text.
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text("\n")
    if len(text.split()) < 40:
        raise JobFetchError("We couldn't find the job description on that page (it may load with JavaScript). "
                            "Paste the job description text instead.")
    return text, title


# --- Text normalisation (shared by job-ad scanning and resume matching) -------
# Words are lower-cased and reduced to a simple stem, so "analyse", "analysis"
# and "analysing" all match, as do "model", "models", "modelling" and "modeling".

_TOKEN_RE = re.compile(r"[a-z0-9]+[+#]*")
_WORD_FAMILIES = [(re.compile(r"^analy[sz]|^analyt"), "analy"), (re.compile(r"^organi[sz]"), "organ")]
_SUFFIXES = [  # (suffix, replacement), checked in this order; only one is removed
    ("isations", ""), ("izations", ""), ("isation", ""), ("ization", ""), ("ations", "at"), ("ation", "at"),
    ("ivity", "ive"), ("ities", ""), ("ity", ""), ("ments", ""), ("ment", ""), ("ness", ""),
    ("ising", ""), ("izing", ""), ("ised", ""), ("ized", ""), ("ises", ""), ("izes", ""), ("ise", ""),
    ("ize", ""), ("ings", ""), ("ing", ""), ("ically", ""), ("ical", ""), ("ial", ""),
    ("ies", "y"), ("ers", ""), ("er", ""), ("ed", ""), ("es", ""), ("al", ""), ("ly", ""), ("s", ""),
]


def _norm(word: str) -> str:
    w = word.lower()
    if len(w) <= 3 or not w.isalpha():
        return w
    for pattern, family in _WORD_FAMILIES:
        if pattern.match(w):
            return family
    for suffix, repl in _SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3 and not (suffix == "s" and w.endswith("ss")):
            w = w[: -len(suffix)] + repl
            if len(w) > 4 and w[-1] == w[-2] and w[-1] not in "aeious":   # modell -> model
                w = w[:-1]
            break
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


class TextIndex:
    """Normalised word index of a piece of text, for phrase and fuzzy lookups."""

    def __init__(self, text: str):
        self.raw = text or ""
        self.words = _TOKEN_RE.findall(self.raw.lower())
        self.norms = [_norm(w) for w in self.words]
        self.positions: dict[str, list[int]] = {}
        for i, n in enumerate(self.norms):
            self.positions.setdefault(n, []).append(i)

    def find(self, phrase: str) -> str | None:
        """Return the matching words if `phrase` appears (normalised), else None."""
        phrase = phrase.strip()
        if len(phrase) == 1:                             # single letters like "R": exact case, whole word
            hit = re.search(r"(?<![A-Za-z0-9+#])" + re.escape(phrase) + r"(?![A-Za-z0-9+#&])", self.raw)
            return phrase if hit else None
        toks = [_norm(t) for t in _TOKEN_RE.findall(phrase.lower())]
        if not toks:
            return None
        for i in self.positions.get(toks[0], []):
            if self.norms[i:i + len(toks)] == toks:
                return " ".join(self.words[i:i + len(toks)])
        return None

    def fuzzy(self, word: str) -> str | None:
        """Close spelling of a single long word (e.g. 'arhictecture'), else None."""
        n = _norm(word)
        if " " in word.strip() or len(n) < SCORING["fuzzy_min_length"]:
            return None
        # Same first letter and similar length: typos rarely change these
        pool = [v for v in self.positions if v[:1] == n[:1] and abs(len(v) - len(n)) <= 2 and len(v) >= 4]
        close = difflib.get_close_matches(n, pool, n=1, cutoff=SCORING["fuzzy_cutoff"])
        return self.words[self.positions[close[0]][0]] if close else None


def _library_synonyms(name: str) -> list[str] | None:
    """Synonyms for a library requirement name (any area), or None if not in the library."""
    key = name.lower().strip()
    for area in AREAS:
        for k, syns in REQUIREMENT_LIBRARY[area].items():
            if k.lower() == key:
                return syns
    return None


# --- Job ad scanning -----------------------------------------------------------
NEUTRAL_SECTION_HEADINGS = [
    "what you'll do", "what you will do", "responsibilities", "key responsibilities", "the role",
    "about the role", "your role", "day to day", "the intern experience", "the program", "the opportunity",
]
# When scanning, some library terms need a more specific trigger than their
# resume synonyms (e.g. an internship ad mentioning "interns" isn't asking for
# internship experience).
SCAN_TRIGGERS = {
    "Internship": ["internship experience", "previous internship", "prior internship", "completed an internship"],
    "Final-year student / recent graduate": ["final year", "final-year", "penultimate year", "recent graduate",
                                             "recent graduates", "recent grad", "recent grads", "graduating in"],
    "Any degree": ["any degree", "any discipline", "all disciplines", "all degrees"],
    # "programming" alone would match "education programs" on a resume, so it's scan-only
    "Coding / programming": ["coding", "coder", "programming", "programmer", "write code"],
}


def _heading(line: str) -> str | None:
    """Classify a short line as a section heading: 'skip', 'req', 'neutral' or None."""
    head = line.lower().replace("’", "'").strip(" :–-")
    if len(head.split()) > 6 or re.search(r"[.!?]$", head):
        return None
    if any(head == h or head.startswith(h + " ") for h in SKIP_SECTION_HEADINGS):
        return "skip"
    if any(head.startswith(h) for h in REQUIREMENT_SECTION_HEADINGS):
        return "req"
    if any(head.startswith(h) for h in NEUTRAL_SECTION_HEADINGS):
        return "neutral"
    return None


def clean_job_lines(job_text: str) -> list[tuple[str, bool]]:
    """Job ad -> list of (line, is_in_requirements_section), with boilerplate removed."""
    out, section = [], "neutral"
    # Split into lines, bullets and clauses, so a cue like "a plus" only affects its own clause
    for line in re.split(r"[\n•●▪·;]|(?<=[.!?])\s+", job_text.replace("’", "'")):
        line = line.strip()
        if not line:
            continue
        kind = _heading(line)
        if kind:
            section = kind
            continue
        if section == "skip" or any(re.search(p, line, flags=re.IGNORECASE) for p in BOILERPLATE_PATTERNS):
            continue
        out.append((line, section == "req"))
    return out


def _alternatives(names_with_evidence: list[tuple[str, str]], line: str) -> bool:
    """True if the found terms form an 'A, B or C' list in the line."""
    low = line.lower()
    spans = []
    for _, ev in names_with_evidence:
        m = re.search(r"\W+".join(map(re.escape, ev.split())), low)
        if not m:
            return False
        spans.append((m.start(), m.end()))
    spans.sort()
    gaps = [low[a[1]:b[0]] for a, b in zip(spans, spans[1:])]
    gap_ok = all(len(re.sub(r"[^\w\s]", " ", g).split()) <= 3 for g in gaps)
    return gap_ok and any(re.search(r"\bor\b|/", g) for g in gaps)


def extract_requirements(job_text: str) -> dict[str, list[str]]:
    """Pick the most important Education / Skills / Experience requirements
    from a job ad. Returns display strings, e.g. 'AI (must-have)'."""
    stats: dict[tuple[str, str], dict] = {}
    for idx, (line, in_req) in enumerate(clean_job_lines(job_text)):
        low = line.lower()
        text = TextIndex(line)
        edu_ok = any(c in low for c in EDUCATION_CONTEXT)
        optional = any(c in low for c in OPTIONAL_CUES)
        must = any(re.search(r"\b" + re.escape(c) + r"\b", low) for c in MUST_CUES)
        found: dict[str, list[tuple[str, str]]] = {a: [] for a in AREAS}

        # 1) Known terms from the library
        for area in AREAS:
            if area == "Education" and not edu_ok:
                continue
            for name, syns in REQUIREMENT_LIBRARY[area].items():
                for trigger in SCAN_TRIGGERS.get(name, syns):
                    ev = text.find(trigger)
                    if ev:
                        found[area].append((name, ev))
                        break

        # 2) Free-text phrases after cues like "experience with ..." (1-3 words)
        known_syns = [s for a in AREAS for n, _ in found[a] for s in (_library_synonyms(n) or [])]
        for m in re.finditer(FREE_TEXT_CUES + r"\s+([^,.;:()\n]{2,60})", line, flags=re.IGNORECASE):
            cue, tail = m.group(1).lower(), m.group(2)
            head = re.split(r"\b(?:and|or|to|so|which|that|who|but|if|when)\b", tail, flags=re.IGNORECASE)[0]
            words = [w for w in head.split() if w.lower() not in FILLER_WORDS][:3]
            phrase = " ".join(words).strip(" -")
            if (not phrase or all(w.lower() in GENERIC_WORDS for w in words)
                    or any(TextIndex(phrase).find(s) for s in known_syns)):
                continue
            area = "Education" if cue in ("degree in", "background in") else \
                   "Experience" if cue.startswith("experience") else "Skills"
            found[area].append((phrase, phrase.lower()))

        # 3) "Finance, Economics or Maths" -> one requirement with alternatives
        for area in ("Education", "Experience"):
            if len(found[area]) >= 2 and _alternatives(found[area], line):
                found[area] = [(" / ".join(n for n, _ in found[area]), "")]

        for area in AREAS:
            for name, _ in found[area]:
                s = stats.setdefault((area, name), {"mentions": 0, "req": False, "must": False,
                                                    "all_optional": True, "first": idx})
                s["mentions"] += 1
                s["req"] |= in_req
                s["must"] |= must and not optional
                s["all_optional"] &= optional

    # A term that is already one of the alternatives in a group is redundant on its own
    for (area, name) in list(stats):
        if " / " in name and _library_synonyms(name) is None:
            for part in name.split(" / "):
                stats.pop((area, part), None)
    # A specific term makes a generic one redundant
    names = {n for _, n in stats}
    for specific, generic in (("Investment banking", "Banking"), ("Computer Science", "Science"),
                              ("Data Science", "Science")):
        if specific in names:
            stats.pop(("Experience", generic), None)
            stats.pop(("Education", generic), None)

    def importance(s):
        return s["mentions"] + 2 * s["req"] + 1.5 * s["must"] - 1.5 * s["all_optional"]

    ranked = sorted(stats.items(), key=lambda kv: (-importance(kv[1]), kv[1]["first"]))[:MAX_KEYWORDS]
    out: dict[str, list[str]] = {a: [] for a in AREAS}
    for (area, name), s in ranked:
        if s["all_optional"] and not s["must"]:
            label = " (preferred)"
        elif (s["req"] and (s["must"] or s["mentions"] >= 2)) or s["mentions"] >= 3:
            label = " (must-have)"
        else:
            label = ""
        out[area].append(name + label)
    return out


# =============================================================================
# 3. AFFINDA: upload a resume, read the results, delete it
# =============================================================================

class AffindaError(Exception):
    """A user-friendly error from talking to Affinda (never contains secrets)."""


def process_with_affinda(pdf_bytes: bytes) -> dict:
    """Upload a resume, wait for processing, return the document JSON, then
    delete the document from Affinda."""
    # The key is only ever placed in this header and never printed or logged.
    headers = {"Authorization": f"Bearer {st.secrets['AFFINDA_API_KEY']}", "Accept": "application/json"}
    doc_id = None
    try:
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()   # safety net if delete fails
        resp = requests.post(
            f"{AFFINDA_BASE_URL}/documents",
            headers=headers,
            files={"file": ("resume.pdf", pdf_bytes, "application/pdf")},       # generic name: no personal data
            data={
                "workspace": st.secrets["AFFINDA_WORKSPACE_ID"],
                "documentType": st.secrets["AFFINDA_DOCUMENT_TYPE_ID"],
                "wait": "true",
                "expiryTime": expiry,
            },
            timeout=AFFINDA_TIMEOUT_S,
        )
        if resp.status_code in (401, 403):
            raise AffindaError("Affinda rejected the credentials. Check the settings in secrets.toml.")
        if not resp.ok:
            raise AffindaError(f"Affinda returned an error (HTTP {resp.status_code}). "
                               "Check the workspace and document type IDs.")
        doc = resp.json()
        doc_id = (doc.get("meta") or {}).get("identifier")

        deadline = time.time() + AFFINDA_TIMEOUT_S
        while not (doc.get("meta") or {}).get("ready") and not (doc.get("meta") or {}).get("failed"):
            if time.time() > deadline:
                raise AffindaError("Processing took too long. Please try again in a minute.")
            time.sleep(3)
            r = requests.get(f"{AFFINDA_BASE_URL}/documents/{doc_id}", headers=headers,
                             params={"format": "json"}, timeout=30)
            r.raise_for_status()
            doc = r.json()
        if (doc.get("meta") or {}).get("failed"):
            raise AffindaError("Affinda couldn't read this PDF. Try a text-based (not scanned) PDF.")
        return doc
    except requests.RequestException:
        raise AffindaError("We couldn't reach Affinda. Please try again shortly.") from None
    finally:
        if doc_id:                                       # always delete the resume from Affinda
            try:
                requests.delete(f"{AFFINDA_BASE_URL}/documents/{doc_id}", headers=headers, timeout=30)
            except requests.RequestException:
                pass


# =============================================================================
# 4. RESUME PARSING: Affinda JSON -> simple dict
# =============================================================================
# Every Affinda value is an "annotation" dict with the clean value in
# "parsed". Tables (workExperience, leadershipAndExtracurriculars) are lists
# of tables, each with parsed.rows[], each row with parsed sub-fields.

def _clean(value) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip().strip(",;|").strip()
    return s or None


def _value(ann) -> str | None:
    if ann is None:
        return None
    if not isinstance(ann, dict):
        return _clean(ann)
    parsed = ann.get("parsed")
    if isinstance(parsed, dict):                         # e.g. linkedinUrl -> {url, domain}
        return _clean(parsed.get("url") or ann.get("raw"))
    return _clean(parsed if parsed is not None else ann.get("raw"))


# The "Student CV" document type has had two field layouts. Both are supported
# because every field is read generically:
#   v1: degree, major, university, relevantCoursework, skills, expectedGraduation,
#       workExperience + leadershipAndExtracurriculars tables
#   v2: education table (qualificationName, majorOrSpecialisation, endDate, ...),
#       technicalSkills, softSkills, softwareAndTools, languages,
#       experience table (roleTitle, organisation, experienceType, bulletPoints)
SKIP_ROW_KEYS = {"startDate", "endDate", "isCurrent", "location"}   # not useful for text matching
NON_TEXT_FIELDS = {"rawText", "phone", "email", "location", "pageCount", "expectedGraduation"}


def _strings(ann) -> list[str]:
    """All text values inside an annotation (recursing into tables/rows)."""
    if ann is None:
        return []
    if isinstance(ann, list):
        return [s for a in ann for s in _strings(a)]
    if not isinstance(ann, dict):
        v = _clean(ann)
        return [v] if v else []
    if "parsed" in ann:
        p = ann["parsed"]
        if isinstance(p, dict) and "rows" in p:
            return _strings(p["rows"])
        if isinstance(p, dict) and any(isinstance(x, (dict, list)) for x in p.values()):
            return [s for k, x in p.items() if k not in SKIP_ROW_KEYS for s in _strings(x)]
        return [v] if (v := _value(ann)) else []
    return []


def _graduation_dates(data: dict) -> list[str]:
    """University graduation / end dates (ISO strings) from either field layout."""
    dates = []
    grad = _value(data.get("expectedGraduation"))
    if grad:
        dates.append(grad)
    for table in data.get("education") or []:
        for row in ((table or {}).get("parsed") or {}).get("rows") or []:
            p = row.get("parsed") or {}
            kind = (_value(p.get("educationType")) or "").lower()
            if "high school" in kind or "secondary" in kind:
                continue
            end = _value(p.get("endDate"))
            if end:
                dates.append(end)
    return dates


def parse_resume(doc: dict) -> dict:
    """Affinda JSON -> what we match against: the WHOLE resume as one text index
    (skills, bullet points, coursework, role titles, certifications, ...)."""
    data = doc.get("data") or {}
    raw = _clean(data.get("rawText")) or ""
    field_text = [s for k, v in data.items() if k not in NON_TEXT_FIELDS for s in _strings(v)]
    name = _value(data.get("fullName")) or ""
    return {
        "name": name.title() if name.isupper() else name,
        "index": TextIndex("\n".join(field_text + [raw])),
        "grad_dates": _graduation_dates(data),
        "has_text": bool(raw or field_text),
    }


# =============================================================================
# 5. MATCHING AND SCORING
# =============================================================================
# Each requirement earns credit from 0 to 1:
#   1.0  the term, a synonym or a normalised form appears anywhere in the resume
#   0.8  a close spelling appears (fuzzy match)
#   0-1  multi-word free-text requirement: share of its words found
# Area score = importance-weighted average credit. Final score = weighted sum
# of area scores; areas with no requirements are left out and their weight is
# shared among the others (an empty area is never a penalty).

def _months_until(iso: str) -> int | None:
    try:
        d = datetime.fromisoformat(iso[:10]).date()
    except ValueError:
        return None
    today = datetime.now().date()
    return (d.year - today.year) * 12 + (d.month - today.month)


def _match_final_year(resume: dict) -> dict:
    months = [m for m in (_months_until(d) for d in resume["grad_dates"]) if m is not None]
    if not months:
        return {"credit": 0.0, "how": "missing", "evidence": "no graduation date found"}
    m = max(months)
    if 0 <= m <= SCORING["final_year_months"]:
        return {"credit": 1.0, "how": "exact", "evidence": f"graduating in {m} months"}
    if -SCORING["recent_grad_months"] <= m < 0:
        return {"credit": 1.0, "how": "exact", "evidence": f"graduated {-m} months ago"}
    if m > SCORING["final_year_months"]:
        return {"credit": SCORING["not_final_year_credit"], "how": "partial",
                "evidence": f"student, graduating in {m} months"}
    return {"credit": 0.0, "how": "missing", "evidence": f"graduated {-m} months ago"}


def _match_any_degree(resume: dict) -> dict:
    for term in ("bachelor", "bachelors", "degree", "master", "diploma", "bsc", "bcom", "mba", "phd", "honours"):
        if ev := resume["index"].find(term):
            return {"credit": 1.0, "how": "exact", "evidence": f"found '{ev}'"}
    return {"credit": 0.0, "how": "missing", "evidence": "no degree found"}


SPECIAL_REQUIREMENTS = {
    "final-year student / recent graduate": _match_final_year,
    "any degree": _match_any_degree,
}


def match_requirement(requirement: str, resume: dict) -> dict:
    """How well one requirement is met: {'credit': 0-1, 'how': ..., 'evidence': ...}."""
    idx: TextIndex = resume["index"]
    key = requirement.lower().strip()
    if key in SPECIAL_REQUIREMENTS:
        return SPECIAL_REQUIREMENTS[key](resume)

    lib = _library_synonyms(requirement)
    known = lib is not None or key in SYNONYMS
    if not known and " / " in requirement:               # alternatives: best one counts
        best = max((match_requirement(alt, resume) for alt in requirement.split(" / ")),
                   key=lambda r: r["credit"])
        return best

    if known:
        candidates = [requirement] + (lib or []) + SYNONYMS.get(key, [])
        for c in candidates:
            if c and (ev := idx.find(c)):
                how = "exact" if _norm(c) == _norm(requirement) else "synonym"
                return {"credit": 1.0, "how": how, "evidence": f"found '{ev}'"}
        for c in candidates:
            if c and (ev := idx.fuzzy(c)):
                return {"credit": SCORING["fuzzy_credit"], "how": "fuzzy", "evidence": f"close spelling '{ev}'"}
        return {"credit": 0.0, "how": "missing", "evidence": "not found"}

    # Free text typed by HR: the whole phrase, else word-by-word partial credit
    if ev := idx.find(requirement):
        return {"credit": 1.0, "how": "exact", "evidence": f"found '{ev}'"}
    words = [w for w in _TOKEN_RE.findall(key) if w not in FILLER_WORDS]
    if not words:
        return {"credit": 0.0, "how": "missing", "evidence": "not found"}
    credits, hits = [], []
    for w in words:
        ev = idx.find(w) or next((e for s in SYNONYMS.get(w, []) if (e := idx.find(s))), None)
        if ev:
            credits.append(1.0); hits.append(ev)
        elif ev := idx.fuzzy(w):
            credits.append(SCORING["fuzzy_credit"]); hits.append(ev)
        else:
            credits.append(0.0)
    credit = sum(credits) / len(credits)
    if credit < SCORING["partial_min"]:
        return {"credit": 0.0, "how": "missing", "evidence": "not found"}
    how = "exact" if credit >= 1 else "partial"
    return {"credit": credit, "how": how, "evidence": f"found {', '.join(repr(h) for h in hits)}"}


_IMPORTANCE_RE = re.compile(r"\s*\((must-have|must have|must|essential|required|preferred|optional|"
                            r"nice to have|desirable)\)\s*$", flags=re.IGNORECASE)


def parse_requirement_box(text: str) -> list[dict]:
    """Tab text -> [{'text': ..., 'importance': 'must-have' | 'normal' | 'preferred'}].
    One requirement per line (commas also split). Add '(must-have)' or
    '(preferred)' at the end of a line to change its weight."""
    seen, out = set(), []
    for line in re.split(r"[\n;]", text or ""):
        m = _IMPORTANCE_RE.search(line)
        importance = "normal"
        if m:
            tag = m.group(1).lower()
            importance = "must-have" if tag in ("must-have", "must have", "must", "essential", "required") \
                else "preferred"
            line = line[: m.start()]
        for item in line.split(","):
            item = item.strip(" -•\t")
            if item and item.lower() not in seen:
                seen.add(item.lower())
                out.append({"text": item, "importance": importance})
    return out


def score_applicant(resume: dict, requirements: dict[str, list[dict]], weights: dict[str, float]) -> dict:
    """Per area: importance-weighted average credit (0-100%).
    Total: weighted sum over areas that have requirements, out of 100."""
    imp_w = SCORING["importance_weights"]
    result = {"areas": {}, "total": 0.0, "shared_weight_from": []}
    for area in AREAS:
        items = []
        for req in requirements[area]:
            m = match_requirement(req["text"], resume)
            items.append({**req, **m, "weight": imp_w[req["importance"]]})
        total_w = sum(i["weight"] for i in items)
        pct = 100 * sum(i["credit"] * i["weight"] for i in items) / total_w if items else None
        result["areas"][area] = {"pct": pct, "items": items}
    active = [a for a in AREAS if result["areas"][a]["pct"] is not None and weights[a] > 0]
    active_w = sum(weights[a] for a in active)
    result["shared_weight_from"] = [a for a in AREAS if weights[a] > 0 and a not in active]
    for a in active:
        share = weights[a] / active_w
        result["areas"][a]["effective_weight"] = 100 * share
        result["total"] += result["areas"][a]["pct"] * share
    return result


# =============================================================================
# 6. USER INTERFACE
# =============================================================================

CSS = """
<style>
  /* Palette: deep green #14343b, white, gold accent #b09670 */
  .block-container { max-width: 900px; padding-top: 2rem; }
  .cv-hero { background: #14343b; border-radius: 14px; padding: 1.6rem 1.8rem; margin-bottom: 1.2rem;
             border-bottom: 4px solid #b09670; }
  .cv-hero h1 { color: #ffffff !important; font-size: 2.1rem; margin: 0; padding: 0; letter-spacing: -0.02em; }
  .cv-hero p { color: #d7e1e1; margin: .3rem 0 0; font-size: 1.02rem; }
  .cv-step { font-size: .78rem; text-transform: uppercase; letter-spacing: .09em; color: #b09670;
             font-weight: 700; margin: 1.8rem 0 -.4rem; }
  h2, h3 { color: #14343b !important; }
  .cv-privacy { font-size: .85rem; color: #3d5a60; background: #f1f5f5; border-left: 3px solid #14343b;
                border-radius: 6px; padding: .6rem .8rem; margin: .4rem 0 .8rem; }
  .stButton > button[kind="primary"], .stDownloadButton > button { border-radius: 8px; }
  .stButton > button[kind="primary"]:hover { background: #b09670; border-color: #b09670; color: #fff; }
  .stTabs [aria-selected="true"] { color: #14343b !important; }
  .cv-total-ok { color: #1a7f4b; font-weight: 600; }
  .cv-total-bad { color: #c2362b; font-weight: 600; }
  .cv-chip { display: inline-block; border-radius: 999px; padding: .1rem .6rem; margin: .15rem .2rem .15rem 0;
             font-size: .85rem; border: 1px solid; }
  .cv-chip.met { background: #e9f7ef; color: #1a7f4b; border-color: #b7e4c7; }
  .cv-chip.miss { background: #fdecea; color: #a8322a; border-color: #f5c2bd; }
  .cv-chip.part { background: #fff4e5; color: #8a4b00; border-color: #ffd8a8; }
  .cv-area { font-weight: 600; margin-top: .4rem; color: #14343b; }
  .cv-footer { text-align: center; color: #b09670; font-size: .8rem; margin-top: 2.5rem; font-weight: 600; }
</style>
"""

PRIVACY_NOTE = ("Resumes are processed by Affinda and deleted immediately after scanning. "
                "Results are kept only in this browser tab and are cleared when you close it.")


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("checks_used", 0)
    ss.setdefault("applicants", [])
    ss.setdefault("uploader_key", 0)
    for area in AREAS:
        ss.setdefault(f"w_{area}", DEFAULT_WEIGHTS[area])
        ss.setdefault(f"req_{area}", "")
    # First visit: show the example job ad with its requirements already filled in
    if not ss.get("defaults_loaded"):
        ss["defaults_loaded"] = True
        ss["job_url"] = DEFAULT_JOB_URL
        if DEFAULT_JOB_TEXT.exists():
            _fill_from_text(DEFAULT_JOB_TEXT.read_text(encoding="utf-8"), DEFAULT_JOB_TITLE)
            ss["scan_msg"] = ("info", f"Example loaded: “{DEFAULT_JOB_TITLE}”. Add the sample applicants "
                                      "below to try it, or paste your own job ad link and click Scan job ad.")


def _fill_from_text(text: str, source: str) -> None:
    found = extract_requirements(text)
    for area in AREAS:
        st.session_state[f"req_{area}"] = "\n".join(found[area])
    n = sum(len(v) for v in found.values())
    if n:
        st.session_state["scan_msg"] = ("success", f"Found {n} requirements in “{source}”. "
                                                   "Review and edit them in the tabs below.")
    else:
        st.session_state["scan_msg"] = ("warning", "No known requirements found. Add them in the tabs below.")


def on_scan_url() -> None:
    url = (st.session_state.get("job_url") or "").strip()
    if not url:
        st.session_state["scan_msg"] = ("warning", "Paste a job ad link first.")
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        text, title = fetch_job_text(url)
    except JobFetchError as e:
        st.session_state["scan_msg"] = ("error", str(e))
        return
    _fill_from_text(text, title[:80])


def on_scan_text() -> None:
    text = st.session_state.get("job_text") or ""
    if len(text.split()) < 10:
        st.session_state["scan_msg"] = ("warning", "Paste the full job description first.")
        return
    _fill_from_text(text, "pasted job description")


def demo_files() -> list[Path]:
    """Saved resumes for demo mode. Prefers the privacy-cleaned copies in
    demo_public/ (committed, so demo mode also works when deployed); falls back
    to the full local originals if those don't exist."""
    public = sorted(PUBLIC_DEMO_DIR.glob("*.json")) if PUBLIC_DEMO_DIR.exists() else []
    if public:
        return public
    local = sorted(DEMO_DIR.glob("*.json")) if DEMO_DIR.exists() else []
    return ([SAMPLE_RESPONSE] if SAMPLE_RESPONSE.exists() else []) + local


def load_demo_resumes() -> dict[str, dict]:
    """Saved Affinda results -> {applicant name: parsed resume}.
    Cached, and refreshed automatically when the demo files change."""
    files = tuple((str(f), f.stat().st_mtime) for f in demo_files())
    return _load_demo_resumes(files, PARSER_VERSION)


PARSER_VERSION = 4          # bump when parse_resume changes so cached demo resumes refresh


@st.cache_data(show_spinner=False)
def _load_demo_resumes(files: tuple, parser_version: int) -> dict[str, dict]:
    out = {}
    for f in (Path(p) for p, _ in files):
        resume = parse_resume(json.loads(f.read_text(encoding="utf-8")))
        out[resume["name"] or f.stem] = resume
    return out


def add_applicant(resume: dict) -> None:
    names = {a["name"] for a in st.session_state["applicants"]}
    base = resume["name"] or f"Applicant {len(st.session_state['applicants']) + 1}"
    name, n = base, 2
    while name in names:
        name, n = f"{base} ({n})", n + 1
    st.session_state["applicants"].append({"id": uuid.uuid4().hex, "name": name, "resume": resume})


def render_weights() -> tuple[dict, bool]:
    st.markdown('<div class="cv-step">Step 1</div>', unsafe_allow_html=True)
    st.subheader("How much does each area matter?")
    cols = st.columns(3)
    for col, area in zip(cols, AREAS):
        col.number_input(f"{area} (%)", min_value=0, max_value=100, step=5, key=f"w_{area}")
    weights = {a: st.session_state[f"w_{a}"] for a in AREAS}
    total = sum(weights.values())
    ok = total == 100
    cls, msg = ("cv-total-ok", "✓ Total 100%") if ok else ("cv-total-bad", f"Total {total}%: must add up to 100%")
    st.markdown(f'<span class="{cls}">{msg}</span>', unsafe_allow_html=True)
    return weights, ok


def render_requirements() -> dict[str, list[str]]:
    st.markdown('<div class="cv-step">Step 2</div>', unsafe_allow_html=True)
    st.subheader("What does the role need?")
    c1, c2 = st.columns([4, 1])
    c1.text_input("Job ad link", key="job_url", placeholder="https://careers.example.com/graduate-analyst",
                  label_visibility="collapsed")
    c2.button("Scan job ad", on_click=on_scan_url, width="stretch")
    with st.expander("Link not working? Paste the job description instead"):
        st.text_area("Job description", key="job_text", height=160, label_visibility="collapsed",
                     placeholder="Paste the full job description here…")
        st.button("Scan pasted text", on_click=on_scan_text)
    if "scan_msg" in st.session_state:
        kind, msg = st.session_state["scan_msg"]
        getattr(st, kind)(msg)

    st.caption("One requirement per line. Scanning fills these in; edit, add or remove anything. "
               "End a line with (must-have) to count it double or (preferred) to count it half. "
               "Use ' / ' for alternatives, e.g. Finance / Economics. An empty tab is skipped and its "
               "weight is shared with the other areas.")
    tabs = st.tabs(AREAS)
    hints = {
        "Education": "e.g. Finance\nMathematics\nEconomics",
        "Skills": "e.g. Excel\nPython\nFinancial modelling",
        "Experience": "e.g. Consulting\nBanking\nClient-facing",
    }
    requirements = {}
    for tab, area in zip(tabs, AREAS):
        with tab:
            st.text_area(f"{area} requirements", key=f"req_{area}", height=170,
                         placeholder=hints[area], label_visibility="collapsed")
            requirements[area] = parse_requirement_box(st.session_state[f"req_{area}"])
            st.caption(f"{len(requirements[area])} requirement(s)")
    return requirements


def render_screening(demo: bool, ready: bool, blockers: list[str]) -> None:
    st.markdown('<div class="cv-step">Step 3</div>', unsafe_allow_html=True)
    st.subheader("Screen an applicant")
    remaining = MAX_CHECKS_PER_SESSION - st.session_state["checks_used"]
    limit_reached = not demo and remaining <= 0

    for b in blockers:
        st.warning(b)

    if demo:
        saved = load_demo_resumes()
        st.caption("Demo mode: uses sample resumes that were scanned once and saved with the app, with "
                   "contact details removed (no Affinda credits used).")
        choice = st.selectbox("Saved resume", list(saved), disabled=not ready)
        c1, c2 = st.columns(2)
        if c1.button("Add this applicant", type="primary", disabled=not ready, width="stretch"):
            add_applicant(saved[choice])
            st.toast(f"Added {choice}")
        if c2.button(f"Add all {len(saved)}", disabled=not ready, width="stretch"):
            for resume in saved.values():
                add_applicant(resume)
            st.toast(f"Added {len(saved)} applicants")
        return

    uploaded = st.file_uploader("Applicant resume (PDF, max 5 MB)", type=["pdf"],
                                key=f"upload_{st.session_state['uploader_key']}", disabled=limit_reached)
    st.markdown(f'<div class="cv-privacy">🔒 {PRIVACY_NOTE}</div>', unsafe_allow_html=True)
    if limit_reached:
        st.info(f"You've used all {MAX_CHECKS_PER_SESSION} resume scans for this session. "
                "This limit saves Affinda credits; open a new session to continue.")
        return
    st.caption(f"{remaining} of {MAX_CHECKS_PER_SESSION} resume scans left this session")

    if st.button("Check resume", type="primary", disabled=not ready, width="stretch"):
        if uploaded is None:
            st.warning("Upload a PDF resume first.")
            return
        pdf_bytes = uploaded.getvalue()                  # in memory only, never written to disk
        if len(pdf_bytes) > MAX_FILE_MB * 1024 * 1024:
            st.warning(f"That file is over {MAX_FILE_MB} MB.")
            return
        if not pdf_bytes.startswith(b"%PDF"):
            st.warning("That doesn't look like a valid PDF.")
            return
        try:
            for k in ("AFFINDA_API_KEY", "AFFINDA_WORKSPACE_ID", "AFFINDA_DOCUMENT_TYPE_ID"):
                if not st.secrets.get(k) or st.secrets.get(k) == "find-me":
                    raise KeyError(k)
        except Exception:
            st.error("Affinda isn't set up yet. Add the IDs to .streamlit/secrets.toml.")
            return
        st.session_state["checks_used"] += 1             # counts as soon as a credit could be spent
        try:
            with st.spinner("Scanning resume… usually 10-30 seconds."):
                doc = process_with_affinda(pdf_bytes)
        except AffindaError as e:
            st.error(str(e))
            return
        finally:
            del pdf_bytes
        resume = parse_resume(doc)
        if not resume["has_text"]:
            st.warning("No text could be read from this PDF (it may be a scanned image).")
            return
        add_applicant(resume)
        st.session_state["uploader_key"] += 1            # clear the uploader for the next resume
        st.rerun()


def _chips(items: list[dict]) -> str:
    """Green = met, amber = partial credit, red = missing. Hover shows the evidence."""
    out = []
    for it in sorted(items, key=lambda i: -i["credit"]):
        cls = "met" if it["credit"] >= 0.999 else "part" if it["credit"] > 0 else "miss"
        label = it["text"]
        if it["importance"] == "must-have":
            label = "★ " + label
        if cls == "part":
            label += f" · {round(it['credit'] * 100)}%"
        tip = f"{it['importance']} · {it['evidence']}"
        out.append(f'<span class="cv-chip {cls}" title="{_esc(tip)}">{_esc(label)}</span>')
    return "".join(out)


def render_applicants(requirements: dict, weights: dict, weights_ok: bool) -> None:
    st.markdown('<div class="cv-step">Step 4</div>', unsafe_allow_html=True)
    st.subheader("Ranked applicants")
    # Drop entries parsed by an older version of the app (e.g. after an update mid-session)
    applicants = st.session_state["applicants"] = [a for a in st.session_state["applicants"]
                                                   if "index" in a["resume"]]
    if not applicants:
        st.caption("Applicants you check will appear here, best match first.")
        return
    if not weights_ok:
        st.warning("Weights must add up to 100% to rank applicants.")
        return

    scored = [(a, score_applicant(a["resume"], requirements, weights)) for a in applicants]
    scored.sort(key=lambda x: x[1]["total"], reverse=True)
    st.caption("Scores update automatically when you change the weights or requirements.")

    def fmt(p):
        return None if p is None else round(p)

    table = pd.DataFrame([{
        "Rank": i + 1,
        "Applicant": a["name"],
        "Match": round(s["total"]),
        **{area: fmt(s["areas"][area]["pct"]) for area in AREAS},
    } for i, (a, s) in enumerate(scored)])
    st.dataframe(
        table, hide_index=True, width="stretch",
        column_config={
            "Rank": st.column_config.NumberColumn(width="small"),
            "Match": st.column_config.ProgressColumn("Match", format="%d%%", min_value=0, max_value=100),
            **{area: st.column_config.NumberColumn(f"{area} ({weights[area]}%)", format="%d%%") for area in AREAS},
        },
    )

    for i, (a, s) in enumerate(scored):
        with st.expander(f"{i + 1}. {a['name']} · {round(s['total'])}% match"):
            for area in AREAS:
                r = s["areas"][area]
                if r["pct"] is None:
                    note = "no requirements, weight shared with the other areas" if weights[area] else "not scored"
                    st.markdown(f'<div class="cv-area">{area}: <span style="color:#8a93a2;font-weight:400">'
                                f'{note}</span></div>', unsafe_allow_html=True)
                    continue
                eff = r.get("effective_weight", 0)
                st.markdown(f'<div class="cv-area">{area}: {round(r["pct"])}% '
                            f'<span style="color:#8a93a2;font-weight:400">(weight {round(eff)}% → '
                            f'{r["pct"] * eff / 100:.1f} points)</span></div>' + _chips(r["items"]),
                            unsafe_allow_html=True)
            st.caption("★ must-have (counts double) · amber = partial credit · hover a requirement to see why")
            if st.button("Remove from list", key=f"rm_{a['id']}"):
                st.session_state["applicants"] = [x for x in applicants if x["id"] != a["id"]]
                st.rerun()

    c1, c2 = st.columns(2)
    c1.download_button("Download list (CSV)", table.to_csv(index=False).encode("utf-8"),
                       file_name="applicant_ranking.csv", mime="text/csv", width="stretch")
    if c2.button("Clear list", width="stretch"):
        st.session_state["applicants"] = []
        st.rerun()


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📄", layout="centered")
    st.markdown(CSS, unsafe_allow_html=True)
    _init_state()

    st.markdown('<div class="cv-hero"><h1>CV Checker</h1>'
                "<p>Score applicants against what the role actually needs based on CV.</p></div>",
                unsafe_allow_html=True)

    demo = False
    if demo_files():
        demo = st.toggle("Demo mode: try it with 3 saved sample resumes (no Affinda credits used)", value=True)

    weights, weights_ok = render_weights()
    requirements = render_requirements()

    blockers = []
    if not weights_ok:
        blockers.append("Set the weights in Step 1 so they add up to 100%.")
    if not any(requirements[a] for a in AREAS):
        blockers.append("Add at least one requirement in Step 2 (scan a job ad or type them in).")
    render_screening(demo, ready=not blockers, blockers=blockers)
    render_applicants(requirements, weights, weights_ok)

    st.markdown('<div class="cv-footer">Powered by Affinda</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
