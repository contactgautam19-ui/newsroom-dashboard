"""The reporting behind an N-Pro script.

Google News search gives a headline and a publisher, nothing more. A script
written from headlines alone invites the model to fill the gaps from memory,
so the newest few reports are opened and read in full first. Everything the
writer is shown is anonymised (S1, S2 ...; outlet names masked) and wrapped so
that article text can never be mistaken for an instruction.

The same corpus is what a finished script is checked against, and what
``trace`` searches when someone asks where a line came from.
"""

import hashlib
import html
import ipaddress
import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import httpx

from app import db
from app.npro import triad as triad_rule
from app.npro.house import Outlets, strip_publisher

MAX_SOURCES = 12          # sources shown to the writer
FULL_TEXT_SOURCES = 6     # of which this many are opened and read
TEXT_CHARS = 3200         # per source
FETCH_TIMEOUT = 3.5       # one request
ENRICH_BUDGET = 6.0       # all of them together
CACHE_HOURS = 36

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-IN,en;q=0.9"}
_tag_re = re.compile(r"<[^>]+>")
_BOILER = re.compile(
    r"subscri|sign in|sign up|log in|newsletter|cookie|all rights reserved|"
    r"follow us|download (?:the|our) app|also read|read more|click here|"
    r"advertis|published\s*[-:]|updated\s*[-:]|photo credit|file photo|"
    r"^\(?(?:photo|image|representational|pic)|whatsapp channel|join our|"
    r"terms of use|privacy policy|copyright|javascript|enable js|"
    r"your browser|free article|premium article|gift this|comments? (?:have|should)|"
    r"disclaimer|this story has not been edited|syndicated feed", re.IGNORECASE)


# ── opening the reports ────────────────────────────────────────────────────

def _public_http(url: str) -> bool:
    """Only ordinary public web addresses are ever fetched."""
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        host = u.hostname
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            return False
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except (ValueError, OSError):
        return False


def _decode_gnews(url: str, client: httpx.Client) -> str | None:
    """Google News RSS links point at a redirect page; resolve to the article."""
    u = urlparse(url)
    if "news.google." not in (u.hostname or ""):
        return url
    m = re.search(r"/articles/([^/?#]+)", u.path)
    if not m:
        return None
    art = m.group(1)
    try:
        page = client.get(f"https://news.google.com/rss/articles/{art}",
                          timeout=FETCH_TIMEOUT)
        sg = re.search(r'data-n-a-sg="([^"]+)"', page.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page.text)
        if not (sg and ts):
            return None
        inner = json.dumps([
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None,
              None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None,
             0, 0, None, 0], art, int(ts.group(1)), sg.group(1)])
        body = "f.req=" + quote(json.dumps([[["Fbv4je", inner, None, "generic"]]]))
        resp = client.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            content=body, timeout=FETCH_TIMEOUT,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        hit = re.search(r'garturlres\\",\\"(https?:[^"\\]+)', resp.text)
        if not hit:
            return None
        return hit.group(1).encode().decode("unicode_escape")
    except (httpx.HTTPError, ValueError, UnicodeError):
        return None


def _paragraphs(page: str) -> list[str]:
    page = re.sub(r"(?is)<(script|style|noscript|svg|form|nav|footer|header|aside)\b.*?</\1>", " ", page)
    out, seen = [], set()
    for raw in re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", page):
        t = re.sub(r"\s+", " ", html.unescape(_tag_re.sub(" ", raw))).strip()
        if len(t) < 80 or _BOILER.search(t[:160]) or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _article_body(page: str) -> list[str]:
    """schema.org articleBody, for sites that do not use plain <p> tags."""
    m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.){200,})"', page)
    if not m:
        return []
    try:
        body = json.loads('"' + m.group(1) + '"')
    except ValueError:
        return []
    body = html.unescape(_tag_re.sub(" ", body))
    return [p.strip() for p in re.split(r"\s*\n+\s*|(?<=[.!?])\s{2,}", body)
            if len(p.strip()) >= 60 and not _BOILER.search(p[:160])]


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower())}


def _fetch_text(item: dict) -> str:
    """The body of one report, or '' when it cannot be read cleanly."""
    try:
        with httpx.Client(headers=_HEADERS, follow_redirects=True,
                          timeout=FETCH_TIMEOUT) as client:
            real = _decode_gnews(item.get("url", ""), client)
            if not real or not _public_http(real):
                return ""
            resp = client.get(real)
            if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
                return ""
            if not _public_http(str(resp.url)):      # redirected somewhere private
                return ""
            page = resp.text[:900_000]
    except (httpx.HTTPError, ValueError, UnicodeError):
        return ""
    paras = _paragraphs(page)
    if len(" ".join(paras)) < 400:
        paras = _article_body(page) or paras
    text = ""
    for p in paras:
        if len(text) + len(p) > TEXT_CHARS:
            break
        text += p + "\n"
    # some sites run paragraphs together with no space after the full stop
    text = re.sub(r"(?<=[a-z0-9)’”\"])([.!?])(?=[A-Z])", r"\1 ", text.strip())
    # a consent wall or the wrong page shares nothing with the headline
    if len(text) < 250 or len(_tokens(item.get("title", "")) & _tokens(text)) < 2:
        return ""
    return text


# ── cache (reports are read once, then reused for checks and tracing) ─────

_mem: dict[str, str] = {}
_table_ready = False


def _key(url: str) -> str:
    return hashlib.sha1((url or "").encode("utf-8", "ignore")).hexdigest()


def _ensure_table(con) -> None:
    global _table_ready
    if not _table_ready:
        con.execute("CREATE TABLE IF NOT EXISTS npro_sources ("
                    "url_key TEXT PRIMARY KEY, body TEXT NOT NULL, "
                    "fetched_at TEXT NOT NULL)")
        _table_ready = True


def _cache_get(urls: list[str]) -> dict[str, str]:
    found = {u: _mem[_key(u)] for u in urls if _key(u) in _mem}
    missing = [u for u in urls if u not in found]
    if not missing:
        return found
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=CACHE_HOURS)).isoformat()
    try:
        with db.connect() as con:
            _ensure_table(con)
            for u in missing:
                row = con.execute(
                    "SELECT body FROM npro_sources WHERE url_key=? AND fetched_at>=?",
                    (_key(u), cutoff)).fetchone()
                if row:
                    found[u] = _mem[_key(u)] = row["body"]
    except Exception:        # the cache is an optimisation, never a failure
        pass
    return found


def _cache_put(fresh: dict[str, str]) -> None:
    now = datetime.now(timezone.utc)
    for u, body in fresh.items():
        _mem[_key(u)] = body
    if len(_mem) > 400:
        for k in list(_mem)[:200]:
            _mem.pop(k, None)
    stored = {u: b for u, b in fresh.items() if b}
    if not stored:
        return
    try:
        with db.connect() as con:
            _ensure_table(con)
            con.execute("DELETE FROM npro_sources WHERE fetched_at<?",
                        ((now - timedelta(hours=CACHE_HOURS * 2)).isoformat(),))
            for u, body in stored.items():
                con.execute("DELETE FROM npro_sources WHERE url_key=?", (_key(u),))
                con.execute("INSERT INTO npro_sources (url_key, body, fetched_at) "
                            "VALUES (?, ?, ?)", (_key(u), body, now.isoformat()))
    except Exception:
        pass


def read_reports(items: list[dict], budget: float = ENRICH_BUDGET,
                 limit: int = FULL_TEXT_SOURCES) -> dict[str, str]:
    """{url: body} for the reports that could be read, within a time budget."""
    targets = [it for it in items if it.get("url")][:limit]
    urls = [it["url"] for it in targets]
    found = _cache_get(urls)
    todo = [it for it in targets if it["url"] not in found]
    if todo:
        pool = ThreadPoolExecutor(max_workers=len(todo))
        futures = {pool.submit(_fetch_text, it): it["url"] for it in todo}
        done, _ = wait(futures, timeout=budget)
        fresh = {}
        for f in done:
            try:
                fresh[futures[f]] = f.result() or ""
            except Exception:
                fresh[futures[f]] = ""
        pool.shutdown(wait=False, cancel_futures=True)
        _cache_put(fresh)
        found.update(fresh)
    return {u: b for u, b in found.items() if b}


# ── the corpus ─────────────────────────────────────────────────────────────

def _age(iso: str) -> str:
    try:
        mins = int((datetime.now(timezone.utc)
                    - datetime.fromisoformat(iso)).total_seconds() // 60)
    except (TypeError, ValueError):
        return ""
    if mins < 0:
        return ""
    if mins < 60:
        return f"{mins}m ago"
    if mins < 2880:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def _plain(text: str) -> str:
    """Source text with anything tag-like removed, so it cannot close or
    forge the wrapper it is placed in."""
    return re.sub(r"\s+", " ", re.sub(r"[<>]", " ", text or "")).strip()


CANDIDATES = 20           # reports weighed when a triad is applied
TRIAD_READ = 10           # of which this many are opened


def _day(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso) + timedelta(hours=5, minutes=30)
        return d.strftime("%d %b %Y").lstrip("0")
    except (TypeError, ValueError):
        return ""


class Corpus:
    """Everything a script may draw on, numbered S1..Sn.

    ``note`` is a reporter's own note ({english, original, language}); it is
    the primary account and is never filtered. ``triad`` is the who / where /
    when the story is pinned to: when given, a report is admitted only if it
    is about the same people and place and carries a date (app/npro/triad.py).
    ``stored`` rebuilds a corpus from a notebook record, exactly as it stood
    when the script was written, without touching the web."""

    def __init__(self, story: dict | None, retrieved: list[dict],
                 topic: str = "", read_full: bool = True, extra: str = "",
                 note: dict | None = None, triad: dict | None = None,
                 stored: list[dict] | None = None):
        self.topic = topic or (story or {}).get("title") or ""
        self.note = note if (note and (note.get("english") or note.get("original"))) else None
        self.triad = triad if triad_rule.is_usable(triad) else None
        self.rejected: list[dict] = []
        self.triad_weak = False
        self.story = story or None
        self.extra = extra or ""     # the editor's own brief: angle, guests ...

        if stored is not None:
            items = [dict(it) for it in stored if it.get("title")]
            bodies = {it.get("url", ""): it.get("text", "") for it in items}
        else:
            cap = CANDIDATES if self.triad else MAX_SOURCES
            items = [it for it in (retrieved or []) if it.get("title")][:cap]
            bodies = {}
            if read_full and items:
                bodies = read_reports(items, limit=TRIAD_READ if self.triad else FULL_TEXT_SOURCES)
        publishers = [it.get("publisher", "") for it in items]
        publishers += (story or {}).get("sources") or []
        if (story or {}).get("publisher"):
            publishers.append(story["publisher"])
        self.outlets = Outlets([p for p in publishers if p], self.topic)

        rows = []
        for it in items:
            title = it.get("title", "") if stored is not None else \
                strip_publisher(it.get("title", ""), it.get("publisher", ""))
            body = bodies.get(it.get("url", ""), "") if stored is None else it.get("text", "")
            rows.append({
                "publisher": it.get("publisher", ""), "url": it.get("url", ""),
                "title": title, "age": _age(it.get("published_at", "")),
                "published_at": it.get("published_at", ""),
                "day": _day(it.get("published_at", "")),
                "text": body, "full": bool(body), "role": it.get("role", ""),
            })
        if self.triad and stored is None:
            kept = []
            for r in rows:
                verdict = triad_rule.judge(r["title"], r["text"], r["published_at"], self.triad)
                if verdict["ok"]:
                    r["role"] = verdict["role"]
                    kept.append(r)
                else:
                    self.rejected.append({"title": r["title"], "publisher": r["publisher"],
                                          "url": r["url"], "why": verdict["why"]})
            if not self.note and len(kept) < 2 and len(rows) > len(kept):
                # the triad was read off a bare headline and matched almost
                # nothing: better to keep the reports and say so than to write
                # from thin air
                self.triad_weak, kept, self.rejected = True, rows, []
            # the development itself first, then background, newest first within each
            kept.sort(key=lambda r: (r["role"] == "background", ), )
            rows = kept[:MAX_SOURCES]
        self.sources = [dict(r, sid=f"S{i}") for i, r in enumerate(rows, 1)]
        self.read_full = sum(1 for s in self.sources if s["full"])

    def left_out(self) -> str:
        """'2 about a different place, 1 with no date' — why reports were dropped."""
        counts: dict[str, int] = {}
        for r in self.rejected:
            counts[r["why"]] = counts.get(r["why"], 0) + 1
        words = {"different people": "about different people",
                 "a different event": "about a different event",
                 "a different place": "about a different place", "no date": "with no date",
                 "a different time": "a similar event at another time"}
        return ", ".join(f"{n} {words.get(w, w)}" for w, n in counts.items())

    # what the checks compare a script against
    def all_text(self) -> str:
        parts = [self.topic, self.extra]
        if self.note:
            parts += [self.note.get("english", ""), self.note.get("original", ""),
                      triad_rule.ascii_digits(self.note.get("original", ""))]
        if self.story:
            parts.append(self.story.get("title", ""))
            parts += [str(e) for e in (self.story.get("evidence_lines") or [])]
        for s in self.sources:
            parts += [s["title"], s["text"]]
        return "\n".join(p for p in parts if p)

    def newest_age(self) -> str:
        return next((s["age"] for s in self.sources if s["age"]), "")

    # what the writer is shown
    def prompt_block(self) -> str:
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        mask = self.outlets.mask
        lines = [f'<reporting now="{now_ist.strftime("%d %b %Y, %I:%M %p")} IST">']
        if self.story:
            lines.append("<desk_story>")
            lines.append("HEADLINE: " + _plain(mask(strip_publisher(
                self.story.get("title", ""), self.story.get("publisher", "")))))
            n = len(self.story.get("sources") or [])
            if n:
                lines.append(f"CARRIED BY: {n} outlet{'s' if n != 1 else ''}")
            for e in (self.story.get("evidence_lines") or [])[:6]:
                lines.append("- " + _plain(mask(str(e)))[:300])
            lines.append("</desk_story>")
        if self.note:
            lines.append('<reporter_note status="our own reporter, primary account">')
            lines.append(_plain(mask(self.note.get("english") or self.note.get("original", "")))[:6000])
            lines.append("</reporter_note>")
        if self.triad:
            t = triad_rule.summary(self.triad)
            lines.append("THE STORY IS PINNED TO: who: " + (", ".join(t["who"]) or "not named")
                         + " | where: " + (", ".join(t["where"]) or "not stated")
                         + " | when: " + t["when"])
        for s in self.sources:
            when = ", ".join(x for x in (s.get("day", ""), s["age"]) if x) or "time unknown"
            role = {"same": ' role="same development"',
                    "background": f' role="background, dated {s.get("day", "")}"'}.get(s.get("role", ""), "")
            lines.append(f'<source id="{s["sid"]}" published="{when}"{role} '
                         f'full_text="{"yes" if s["full"] else "no, headline only"}">')
            lines.append("HEADLINE: " + _plain(mask(s["title"])))
            if s["text"]:
                lines.append("TEXT: " + _plain(mask(s["text"])))
            lines.append("</source>")
        if not self.sources and self.note:
            lines.append("No other reporting passed the who / where / when check. Write "
                         "from the reporter's note alone and add no background.")
        elif not self.sources:
            lines.append("No reporting could be pulled. Say so; do not draft from memory.")
        lines.append("</reporting>")
        cc = self.cross_check()
        if cc["shared"] or cc["single"]:
            lines.append("")
            lines.append("DESK CROSS-CHECK (computed, not written by a reporter):")
            if cc["shared"]:
                lines.append("- Figures that appear in two or more sources: "
                             + ", ".join(cc["shared"][:14]))
            if cc["single"]:
                lines.append("- Figures that appear in only one source (word as "
                             "\"is being reported\" unless an official is quoted, and "
                             "list in PRODUCER NOTES): " + ", ".join(cc["single"][:14]))
        if self.sources and not self.read_full:
            lines.append("")
            lines.append("NOTE: only headlines could be read. Keep the script short "
                         "and say nothing the headlines do not support.")
        return "\n".join(lines)

    def cross_check(self) -> dict:
        """Which figures are corroborated across reports and which rest on one."""
        if hasattr(self, "_cc"):
            return self._cc
        seen: dict[str, set[str]] = {}
        for s in self.sources:
            for fig in set(_figures(s["title"] + " " + s["text"])):
                seen.setdefault(fig, set()).add(s["sid"])
        multi = len(self.sources) > 1
        shared = sorted((f for f, ids in seen.items() if len(ids) > 1), key=_fig_sort)
        single = sorted((f for f, ids in seen.items() if len(ids) == 1 and multi),
                        key=_fig_sort)
        self._cc = {"shared": shared, "single": single}
        return self._cc


_FIG_RE = re.compile(
    r"(?P<cur>Rs\.?\s?|₹\s?|\$\s?|€\s?|£\s?)?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?P<scale>\s?(?:%|per cent|percent|crore|lakh|million|billion|trillion))?",
    re.IGNORECASE)
_MONTH = (r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|"
          r"march|april|june|july|august|september|october|november|december)")


def _figures(text: str) -> list[str]:
    """Figures worth cross-checking between reports: money, percentages,
    scaled amounts and plain counts of two digits or more. Dates, years and
    clock times are left out; '45 dead' and 'kills 45' count as the same 45."""
    text = text or ""
    out = []
    for m in _FIG_RE.finditer(text):
        num = m.group("num").replace(",", "")
        scale = re.sub(r"\s+", " ", (m.group("scale") or "").strip().lower()).replace("percent", "per cent")
        cur = "Rs " if (m.group("cur") or "").strip().lower().startswith(("rs", "₹")) else (m.group("cur") or "").strip()
        before = text[max(0, m.start() - 12):m.start()].lower()
        after = text[m.end():m.end() + 12].lower()
        if not (scale or cur):
            if len(num.replace(".", "")) < 2 or re.fullmatch(r"(19|20)\d\d", num):
                continue
            if re.match(rf"\s*{_MONTH}\b", after) or re.search(rf"\b{_MONTH}\.?\s*$", before):
                continue                                  # a date
            if re.match(r"\s*(?::\d\d|am\b|pm\b|a\.m|p\.m|st\b|nd\b|rd\b|th\b)", after):
                continue                                  # a time or an ordinal
        out.append(f"{cur}{num}{' ' + scale if scale and scale != '%' else scale}".strip())
    return out


def _fig_sort(fig: str):
    return (-len(re.sub(r"[^\d]", "", fig)), fig)


# ── where did this line come from? ─────────────────────────────────────────

_STOP = set(
    "the a an and or of in on to for with at by from as is are was were be been "
    "has have had it its this that these those will would could should may might "
    "not but than then into over after before amid about their his her they he "
    "she we you our who what when where why how which also more most now new "
    "says said say being framed described presented reported".split())


def _content(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9'\-]*", (text or "").lower())
            if (len(w) > 2 or w.isdigit()) and w not in _STOP]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?।])\s+|\n+", text or "")
            if len(s.strip()) > 25]


def trace(line: str, corpus: Corpus, top: int = 3) -> dict:
    """Find the reporting a line of script rests on. Purely mechanical: the
    line's own words are matched against the sentences of each report, so the
    answer is what the reports say, never a model's recollection."""
    want = _content(line)
    want_set = set(want)
    if len(want_set) < 3:
        return {"line": line, "matches": [], "confidence": "none"}
    nums = {w for w in want_set if any(c.isdigit() for c in w)}
    hits = []
    pool = list(corpus.sources)
    if corpus.note:
        pool.insert(0, {"sid": "NOTE", "publisher": "Reporter's note", "url": "",
                        "title": "", "age": "", "role": "note",
                        "text": corpus.note.get("english", "")})
    for s in pool:
        best, best_sent = 0.0, ""
        for sent in [s["title"]] + _sentences(s["text"]):
            have = set(_content(sent))
            if not have:
                continue
            overlap = want_set & have
            score = len(overlap) / len(want_set)
            if nums and nums <= have:
                score += 0.15                   # the figure itself is there
            if score > best:
                best, best_sent = score, sent
        if best >= 0.34:
            hits.append({"sid": s["sid"], "publisher": s["publisher"],
                         "title": s["title"], "url": s["url"], "age": s["age"],
                         "day": s.get("day", ""), "role": s.get("role", ""),
                         "excerpt": best_sent[:260], "score": round(min(best, 1.0), 2)})
    hits.sort(key=lambda h: h["score"], reverse=True)
    hits = hits[:top]
    confidence = ("none" if not hits else
                  "strong" if hits[0]["score"] >= 0.6 else "partial")
    return {"line": line, "matches": hits, "confidence": confidence,
            "corroborated": sum(1 for h in hits if h["score"] >= 0.5)}
