"""Checks every N-Pro script passes before an editor sees it.

Two kinds. Form: the right sections in the right order, no outlet names, no
placeholders, no markdown, headlines that fit. Grounding: every figure and
every quotation in the copy must be findable in the reporting the script was
written from, and names that appear nowhere in that reporting are flagged.

Nothing here calls a model. A failed check is either repaired by one rewrite
pass (engine) or, if that does not clear it, fixed mechanically where that is
safe and otherwise surfaced to the producer. It is never silently passed.
"""

import re
from datetime import datetime, timedelta, timezone

from app.npro import house

HARD, SOFT = "hard", "soft"

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_WORD_FOR = {v: k for k, v in _NUM_WORDS.items()}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
_UNITS = (r"people|persons|dead|killed|deaths|injured|wounded|arrested|detained|"
          r"missing|crore|lakh|million|billion|trillion|thousand|hundred|per cent|"
          r"percent|seats|votes|days|weeks|months|years|hours|minutes|km|"
          r"kilometres|kilometers|soldiers|students|children|women|men|cases|"
          r"states|districts|countries|members|mlas|mps|candidates|workers|"
          r"passengers|officers|personnel|firs|accused|victims|bodies|flights|"
          r"trains|runs|wickets|goals|points|times|phases|rounds")
_SPELLED_RE = re.compile(
    r"\b((?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|"
    r"three|four|five|six|seven|eight|nine))?|two|three|four|five|six|seven|eight|"
    r"nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen)\s+(?:(?:more|other|new|fresh|senior|former)\s+)?(?:" + _UNITS + r")\b",
    re.IGNORECASE)
_DIGIT_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\d])")


def _spelled_value(phrase: str) -> int | None:
    parts = re.split(r"[- ]", phrase.lower())
    try:
        return sum(_NUM_WORDS[p] for p in parts)
    except KeyError:
        return None


def _norm_num(tok: str) -> str:
    t = tok.replace(",", "").rstrip(".")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t


def _number_facts(text: str) -> tuple[set[str], set[str]]:
    """(digit numbers, number words) found in a body of text."""
    digits = {_norm_num(m.group(0)) for m in _DIGIT_RE.finditer(text or "")}
    words = set(re.findall(r"[a-z]+", (text or "").lower())) & (
        set(_NUM_WORDS) | set(_ORDINALS))
    for m in re.finditer(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
                         r"[- ](one|two|three|four|five|six|seven|eight|nine)\b",
                         (text or "").lower()):
        digits.add(str(_NUM_WORDS[m.group(1)] + _NUM_WORDS[m.group(2)]))
    return digits, words


def _squash(text: str) -> str:
    """Lowercase letters and digits only: how quotations are compared."""
    return re.sub(r"[^a-z0-9ऀ-ൿ]+", " ", (text or "").lower()).strip()


_QUOTE_RE = re.compile(r"[“\"]([^“”\"\n]{3,400})[”\"]|‘([^‘’\n]{3,200})’")

# Capitalised words that are not names, or are names so universal that
# flagging them would only be noise.
_NAME_OK = set("""
i a an the and but or so yet if in on at to for of by with from as is are was
were be been it its this that these those he she they we you his her their our
who what when where why how which while after before since until now then today
tomorrow yesterday tonight meanwhile however still also there here not no yes
monday tuesday wednesday thursday friday saturday sunday january february march
april may june july august september october november december jan feb mar
apr jun jul aug sep sept oct nov dec ist gmt india indian
indians government centre central state states union parliament opposition
minister ministry prime chief deputy president vice governor court supreme high
police army navy air force lok sabha rajya assembly council cabinet commission
election elections mla mlas mp mps cm pm gfx vo sot anchor outcue map graphic
graphics lower third full screen breaking news question questions panel
panellist panelist panelists panellists guest guests studio viewers good
evening morning afternoon night welcome tonight's bjp congress
""".split())


# Words that size an event up. A script may use one only if the reporting
# does: a "fire" does not become a "major fire" on the way to air.
_SIZING = set("""major massive huge enormous devastating deadly horrific shocking
unprecedented historic landmark sensational dramatic catastrophic tragic brutal
gruesome stunning explosive sweeping record-breaking mega giant colossal fierce
raging widespread severe critical grave alarming""".split())


def _vocab(text: str) -> set[str]:
    return set(re.findall(r"[a-zऀ-ൿ][a-zऀ-ൿ'’\-]*", (text or "").lower()))


def _name_tokens(body: str) -> list[str]:
    """Capitalised words inside sentences (so not merely starting one)."""
    out = []
    for sent in re.split(r"(?<=[.!?:])\s+|\n+", body or ""):
        words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", sent)
        for i, w in enumerate(words):
            if not w[0].isupper() or len(w) < 3:
                continue
            if i == 0:
                continue            # sentence openers are capitalised anyway
            out.append(w)
    return out


def _issue(level, code, detail, section=""):
    return {"level": level, "code": code, "detail": detail, "section": section}


def _duration_words(params: dict) -> int | None:
    """Rough word budget for the timed read (about 2.6 words a second)."""
    d = str((params or {}).get("duration") or (params or {}).get("length") or "")
    m = re.search(r"(\d+)\s*(sec|min)", d.lower())
    if not m:
        return None
    secs = int(m.group(1)) * (60 if m.group(2) == "min" else 1)
    return int(secs * 2.6)


def fit_headlines(script: str, limit: int = 40) -> str:
    """Drop headline options that run past the on-screen limit, as long as at
    least one option fits. A headline is never shortened by machine."""
    lines = (script or "").splitlines()
    out, in_heads, kept, dropped = [], False, [], []
    for line in lines:
        m = house._LABEL_RE.match(line)
        if m and house.is_known_label(re.sub(r"\s+", " ", m.group(1).strip())):
            if in_heads:
                out += kept or dropped
            in_heads = m.group(1).strip() == "HEADLINES"
            kept, dropped = [], []
            out.append(line)
            continue
        if in_heads and line.strip():
            text = re.sub(r"^[\s\-•\d.)]+", "", line).strip()
            (kept if len(text) <= limit else dropped).append(line)
        elif in_heads:
            out += kept or dropped
            kept, dropped, in_heads = [], [], False
            out.append(line)
        else:
            out.append(line)
    if in_heads:
        out += kept or dropped
    return "\n".join(out)


def tidy(script: str) -> str:
    """Mechanical clean-ups that are always safe."""
    t = house.strip_markdown(script or "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    return fit_headlines(re.sub(r"\n{3,}", "\n\n", t).strip())


def check_script(script: str, format_id: str | None, corpus, params: dict | None = None,
                 allow_text: str = "", translated: bool = False) -> list[dict]:
    """Every problem found in ``script``. ``allow_text`` is extra material the
    copy may legitimately draw on (the script being rewritten, for a smart
    action). ``translated`` relaxes the checks that assume English."""
    issues: list[dict] = []
    spec = house.FORMATS.get(format_id or "")
    sections = house.split_sections(script, known_only=bool(spec))
    labels = [lab for lab, _ in sections if lab]

    # ── form ──
    if spec:
        pos = -1
        for need in spec["required"]:
            if need not in labels:
                issues.append(_issue(HARD, "missing_section",
                                     f"The {need} section is missing."))
                continue
            at = labels.index(need)
            if at < pos:
                issues.append(_issue(SOFT, "section_order",
                                     f"{need} is out of order."))
            pos = max(pos, at)
        known = set(spec["required"]) | set(spec["optional"])
        for lab in labels:
            if lab not in known and not re.fullmatch(r"VO \d|GFX(?: \d)?|SOT(?: \d)?", lab):
                issues.append(_issue(SOFT, "unknown_section",
                                     f"Unexpected section label {lab}."))
        for lab, body in sections:
            if lab in spec["required"] and lab != house.NOTES_LABEL and len(body.strip()) < 12:
                issues.append(_issue(HARD, "empty_section",
                                     f"The {lab} section is empty."))

    if house.MARKDOWN_RE.search(script or ""):
        issues.append(_issue(HARD, "markdown", "Markdown symbols in the script."))

    for lab, body in sections:
        if lab == "HEADLINES":
            heads = [re.sub(r"^[\s\-•\d.)]+", "", h).strip()
                     for h in body.splitlines() if h.strip()]
            if spec and len(heads) != 3:
                issues.append(_issue(SOFT, "headline_count",
                                     f"{len(heads)} headline options instead of three."))
            for h in heads:
                if len(h) > 40:
                    issues.append(_issue(SOFT, "headline_length",
                                         f'Headline over 40 characters: "{h}"', lab))

    copy, _notes = house.split_notes(script)
    for name in corpus.outlets.find(script or ""):
        issues.append(_issue(HARD, "outlet_name",
                             f'Names an outlet: "{name}". Reword in the newsroom\'s own voice.'))
    for m in house.PLACEHOLDER_RE.finditer(copy):
        issues.append(_issue(HARD, "placeholder",
                             f'Placeholder in finished copy: "{m.group(0)}". Cut it and write around the gap.'))
    for m in house.SOURCE_TAG_RE.finditer(script or ""):
        if re.search(r"S\d", m.group(0)):
            issues.append(_issue(HARD, "source_tag",
                                 f'Mentions a source number: "{m.group(0)}".'))

    # ── grounding ──
    ground = corpus.all_text() + "\n" + (allow_text or "")
    g_digits, g_words = _number_facts(ground)
    g_squash = _squash(ground)
    g_vocab = _vocab(ground)
    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    g_digits |= {str(now.year), str(now.day)}
    budget_nums = {m for m in re.findall(r"\d+", " ".join(
        str(v) for v in (params or {}).values() if isinstance(v, str)))}

    spoken = set(spec["spoken"]) if spec else None
    for lab, body in sections:
        if lab == house.NOTES_LABEL:
            continue
        hard_here = spoken is None or lab in spoken or re.fullmatch(r"VO \d", lab)
        level = HARD if hard_here else SOFT
        text = re.sub(r"(?m)^\s*(?:\d{1,2}[.)]|Q\d{1,2}[.:)])\s+", "", body)   # list numbering
        for m in _DIGIT_RE.finditer(text):
            n = _norm_num(m.group(0))
            if not n or n in g_digits or n in budget_nums:
                continue
            if n.isdigit() and int(n) in _WORD_FOR and _WORD_FOR[int(n)] in g_words:
                continue
            issues.append(_issue(level, "figure",
                                 f'The figure "{m.group(0)}" is not in the reporting.', lab))
        for m in _SPELLED_RE.finditer(text):
            val = _spelled_value(m.group(1))
            word = m.group(1).lower()
            if val is None or str(val) in g_digits or word in g_words \
                    or word.split("-")[0] in g_words and "-" not in word:
                continue
            issues.append(_issue(level, "figure",
                                 f'The figure "{m.group(0)}" is not in the reporting.', lab))
        for m in _QUOTE_RE.finditer(body):
            q = (m.group(1) or m.group(2) or "").strip()
            sq = _squash(q)
            if not sq or sq in g_squash:
                continue
            long_quote = len(sq.split()) >= 4
            issues.append(_issue(
                level if long_quote else SOFT, "quote",
                f'The quoted words "{q[:90]}" do not appear in the reporting.', lab))
        if not translated:
            for w in sorted(set(re.findall(r"[a-z\-]+", body.lower())) & _SIZING):
                if w not in g_vocab:
                    issues.append(_issue(level, "sizing",
                                         f'"{w}" sizes the story up, and the reporting does not use '
                                         "that word. Describe what happened instead.", lab))
        if not translated and lab not in ("HEADLINES", "POINTERS", "GFX"):
            missing = []
            for w in _name_tokens(body):
                base = w.lower().replace("’", "'")
                base = re.sub(r"'s$", "", base)
                if base in g_vocab or base in _NAME_OK or base.rstrip("s") in g_vocab:
                    continue
                if w not in missing:
                    missing.append(w)
            for w in missing:
                issues.append(_issue(SOFT, "name",
                                     f'"{w}" does not appear in the reporting.', lab))

    # ── length against the brief ──
    budget = _duration_words(params or {})
    if budget and spec:
        main = {"av_read": "ANCHOR READ", "custom": "SCRIPT"}.get(format_id)
        body = next((b for lab, b in sections if lab == main), "")
        words = len(body.split())
        if main and words > budget * 1.45:
            issues.append(_issue(SOFT, "too_long",
                                 f"The {main.lower()} runs about {words} words, roughly "
                                 f"{round(words / 2.6)} seconds at an anchor's pace; the brief "
                                 f"asked for about {round(budget / 2.6)}."))

    # one entry per distinct problem
    seen, out = set(), []
    for i in issues:
        k = (i["code"], i["detail"])
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out


def hard(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i["level"] == HARD]


def repair_brief(issues: list[dict], claims: list[str] | None = None) -> str:
    """The editor's list handed to the rewrite pass."""
    lines = []
    for i in issues:
        if i["level"] == HARD or i["code"] in ("name", "quote"):
            where = f" ({i['section']})" if i.get("section") else ""
            lines.append(f"- {i['detail']}{where}")
    for c in claims or []:
        lines.append(f"- Not supported by the reporting: {c}")
    return "\n".join(lines[:24])


def force_clean(script: str, corpus) -> str:
    """What is fixed mechanically when the rewrite pass still left a problem:
    outlet names, source numbers, placeholders and markdown are removed.
    Unsupported figures are not touched here; they go to the producer."""
    t = tidy(script)
    t = corpus.outlets.scrub(t)
    t = house.SOURCE_TAG_RE.sub(lambda m: "" if re.search(r"S\d", m.group(0)) else m.group(0), t)
    copy, notes = house.split_notes(t)
    copy = house.PLACEHOLDER_RE.sub("", copy)
    copy = re.sub(r"\(\s*\)", "", copy)
    copy = re.sub(r"[ \t]{2,}", " ", copy)
    copy = re.sub(r"\s+([,.;:!?])", r"\1", copy)
    copy = re.sub(r"(?m)^[ \t]*[,;.][ \t]*", "", copy)
    return copy.rstrip()


def producer_flags(issues: list[dict], limit: int = 5) -> list[str]:
    """Check-before-air lines for whatever could not be cleared."""
    out = []
    for i in issues:
        if i["code"] in ("figure", "quote") and i["level"] == HARD:
            out.append("Check before air: " + i["detail"])
    names = [re.match(r'"([^"]+)"', i["detail"]).group(1)
             for i in issues if i["code"] == "name"]
    if names:
        out.append("Confirm these names against the reports: " + ", ".join(names[:6]) + ".")
    out += [i["detail"] for i in issues if i["code"] == "too_long"]
    return out[:limit]


def summarise(issues: list[dict], repaired: bool, corpus) -> dict:
    """The verdict shown beside the script."""
    h = hard(issues)
    status = "review" if h else "passed"
    lines = []
    lines.append("No outlet is named in the script."
                 if not any(i["code"] == "outlet_name" for i in issues)
                 else "An outlet name could not be removed.")
    bad_figs = [i for i in issues if i["code"] == "figure" and i["level"] == HARD]
    lines.append("Every figure matches the reports." if not bad_figs
                 else f"{len(bad_figs)} figure(s) need checking before air.")
    bad_q = [i for i in issues if i["code"] == "quote" and i["level"] == HARD]
    lines.append("Every quotation appears in the reports." if not bad_q
                 else f"{len(bad_q)} quotation(s) need checking before air.")
    if not corpus.read_full and corpus.sources:
        lines.append("Written from headlines only; the full reports could not be opened.")
    for i in h:
        if i["code"] not in ("outlet_name", "figure", "quote", "sizing"):
            lines.append(i["detail"])
    return {"status": status, "repaired": repaired, "lines": lines,
            "sources": len(corpus.sources), "read_full": corpus.read_full}
