import json
import os
import re
import time

from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from schemas import DayStructure, GenerationSpec, SessionRequirement

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# This org allows 8000 tokens per request, input plus reserved output. Output
# room is computed from the actual input size instead of a fixed max_tokens,
# so a longer input trades output headroom for input rather than tripping a 413.
REQUEST_TOKEN_CEILING = 7600
FIXED_OVERHEAD_TOKENS = 1300
CHARS_PER_TOKEN = 3.0
MIN_OUTPUT_TOKENS = 2000
MAX_OUTPUT_TOKENS = 4000
REPAIR_OUTPUT_TOKENS = 1800

_DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
         "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
_KINDS = {"class": "class", "lecture": "class", "theory": "class",
          "lab": "lab", "practical": "lab", "special": "special"}


class CompactSpec(BaseModel):
    title: str = ""
    working_days: list[str] = Field(default_factory=list)
    period_start_times: list[str] = Field(default_factory=list)
    period_length_minutes: int = 50
    rows: list[str] = Field(default_factory=list)


class RowsOnly(BaseModel):
    rows: list[str] = Field(default_factory=list)


_PROMPT = (
    "Extract a weekly timetable requirement from the text below.\n\n"
    "Return ONE JSON object (never an array) with:\n"
    "- title: short name for the timetable\n"
    "- working_days: the weekdays it runs\n"
    "- period_start_times: HH:MM (24-hour) start of EVERY teaching period, in order, EXCLUDING "
    "lunch. Lunch is a break between two periods, never a period itself, even when the text "
    "writes it as 'X to Y' like the periods. Example: '9:50-10:40, 10:40-11:30, 11:30-12:20, "
    "12:20-1:10, lunch 1:10-1:50, 1:50-2:40, 2:40-3:30, 3:30-4:20' gives exactly 7 periods: "
    "09:50,10:40,11:30,12:20,13:50,14:40,15:30. Never repeat a start time.\n"
    "- period_length_minutes\n"
    "- rows: one string per distinct subject/activity PER SECTION, exactly 8 fields separated "
    "by '|':\n"
    "  section|subject|kind|minutes|weekly_count|faculty|venue|daily_cap_minutes\n"
    "  section = label as written (A, B, C...), or empty if the text describes only one "
    "section; kind = class (one period), lab (long practical) or special (research, sports, "
    "seminar, VAP...); minutes = length of ONE occurrence; weekly_count = times per week, read "
    "precisely; faculty = names separated by ';' or empty; venue = a specific room or lab only "
    "if stated, else empty; daily_cap_minutes = a per-day limit for this subject only if "
    "explicitly stated, else empty.\n"
    "  Example row: C|Machine Learning|class|50|5|Mrs. Anitha||\n\n"
    "Rules: write a row for EVERY subject of EVERY section - never stop early, never merge "
    "sections, never skip a section because it looks similar to another. Put all sections in "
    "the same rows list. Use the same full subject name for the same subject in every "
    "section, and write each person's name identically every time. Use only what the text "
    "states - never invent counts, durations, names or rooms; leave a field empty rather "
    "than guessing.\n\n"
    "Text:\n"
)

_REPAIR_PROMPT = (
    "The text below describes timetable requirements for several sections. For Section "
    "{section}, only these subjects were found so far: {have}.\n"
    "Find every OTHER subject or activity the text describes for Section {section} and "
    "return them as rows in this exact 8-field format (section field = {section}):\n"
    "section|subject|kind|minutes|weekly_count|faculty|venue|daily_cap_minutes\n"
    "Do NOT repeat subjects already listed. If there are none, return an empty list. "
    "Never invent anything the text doesn't state.\n\n"
    "Text:\n"
)


def _make_llm(max_tokens):
    return ChatGroq(
        api_key=GROQ_API_KEY, model="openai/gpt-oss-120b", temperature=0,
        max_tokens=max_tokens, reasoning_effort="low", max_retries=3,
    )


def _retry_after_seconds(message):
    if "Request too large" in message:
        return None  # permanent for this request size - waiting won't change it
    if "rate_limit_exceeded" not in message and "429" not in message:
        return None
    m = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)(ms|s)", message)
    if not m:
        return None
    seconds = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
    return int(m.group(1) or 0) * 60 + seconds


def _invoke_with_backoff(structured_llm, prompt, attempts=3):
    for attempt in range(attempts):
        try:
            return structured_llm.invoke(prompt)
        except Exception as error:
            wait = _retry_after_seconds(str(error))
            if wait is None or attempt == attempts - 1:
                raise
            print(f"[requirement_extractor] rate limited, waiting {wait:.0f}s before retry")
            time.sleep(min(wait + 1, 45))


def _strip_fences(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    return t


def _norm_time(value):
    m = re.search(r"(\d{1,2})\s*[:.\-]\s*(\d{2})\s*([ap]m)?", str(value or ""), re.I)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    suffix = (m.group(3) or "").lower()
    if suffix == "pm" and h < 12:
        h += 12
    if suffix == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    return f"{h:02d}:{mi:02d}"


def _norm_section(label):
    s = re.sub(r"^\s*(section|sec\.?|batch|group)\s*[-:]?\s*", "", str(label or ""), flags=re.I).strip()
    return s.upper() if len(s) <= 3 else s


def _subject_key(name):
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _parse_rows(rows, period_len):
    sessions, notes, seen = [], [], set()
    for raw in rows:
        line = str(raw).strip().strip("`").strip()
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        parts += [""] * (8 - len(parts))
        section, subject, kind, minutes, count, faculty, venue, cap = parts[:8]
        if subject.lower() == "subject" or section.lower() == "section":
            continue  # a header row the model echoed
        m, c = re.search(r"\d+", minutes), re.search(r"\d+", count)
        if not subject or not m or not c:
            notes.append(f"Skipped a row I couldn't read: {line[:70]}")
            continue
        duration, weekly = int(m.group()), int(c.group())
        if duration <= 0 or weekly <= 0:
            continue
        section = _norm_section(section)
        key = (section.lower(), _subject_key(subject))
        if key in seen:
            continue
        seen.add(key)
        resolved_kind = _KINDS.get(kind.lower())
        if resolved_kind is None:
            resolved_kind = "class" if duration <= period_len else "special"
        cap_match = re.search(r"\d+", cap)
        sessions.append(SessionRequirement(
            subject_name=subject, session_kind=resolved_kind, duration_minutes=duration,
            weekly_count=weekly, faculty=[n.strip() for n in re.split(r"[;/]", faculty) if n.strip()],
            max_same_day_minutes=int(cap_match.group()) if cap_match else None,
            section=section, venue=venue,
        ))
    return sessions, notes


def _resolve_section_labels(sessions, notes):
    """An unlabeled session used to silently become an invisible extra section."""
    labels = {s.section for s in sessions if s.section}
    unlabeled = [s for s in sessions if not s.section]
    if not unlabeled or not labels:
        return sessions
    if len(labels) == 1:
        only = next(iter(labels))
        return [s if s.section else s.model_copy(update={"section": only}) for s in sessions]
    names = ", ".join(s.subject_name for s in unlabeled[:4])
    notes.append(f"{len(unlabeled)} row(s) had no section label and were left out ({names}"
                 f"{'...' if len(unlabeled) > 4 else ''}). Name the section next to each subject if they matter.")
    return [s for s in sessions if s.section]


def _section_sizes(sessions):
    sizes = {}
    for s in sessions:
        if s.section:
            sizes.setdefault(s.section, set()).add(_subject_key(s.subject_name))
    return sizes


def _incomplete_sections(sessions):
    sizes = _section_sizes(sessions)
    if len(sizes) < 2:
        return []
    largest = max(len(v) for v in sizes.values())
    return [sec for sec, subs in sizes.items() if len(subs) < max(2, 0.7 * largest)]


def _repair_section(text, section, sessions, period_len, notes):
    have = sorted({s.subject_name for s in sessions if s.section == section})
    prompt = _REPAIR_PROMPT.format(section=section, have="; ".join(have) or "none") + text
    try:
        structured = _make_llm(REPAIR_OUTPUT_TOKENS).with_structured_output(RowsOnly, method="json_schema", strict=False)
        out = _invoke_with_backoff(structured, prompt)
    except Exception as error:
        print(f"[requirement_extractor] repair read for section {section} failed: {error}")
        return sessions
    new_sessions, parse_notes = _parse_rows(out.rows, period_len)
    notes.extend(parse_notes)
    existing = {_subject_key(s.subject_name) for s in sessions if s.section == section}
    added = []
    for s in new_sessions:
        if s.section and s.section != section:
            continue
        s = s if s.section else s.model_copy(update={"section": section})
        if _subject_key(s.subject_name) in existing:
            continue
        existing.add(_subject_key(s.subject_name))
        added.append(s)
    if added:
        notes.append(f"Section {section} first came back with only {len(have)} subjects, so I re-read "
                     f"your text for it and found {len(added)} more.")
    return sessions + added


def _recover_compact(raw_message):
    data = json.loads(_strip_fences(getattr(raw_message, "content", "") or ""))
    items = [i for i in (data if isinstance(data, list) else [data]) if isinstance(i, dict)]
    if not items:
        raise ValueError("Unrecognized output shape from the model")
    first = items[0]
    return CompactSpec(
        title=str(first.get("title") or ""),
        working_days=[str(d) for d in (first.get("working_days") or [])],
        period_start_times=[str(t) for t in (first.get("period_start_times") or [])],
        period_length_minutes=int(first.get("period_length_minutes") or 50),
        rows=[str(r) for item in items for r in (item.get("rows") or [])],
    )


def _call_compact(text):
    est_input = FIXED_OVERHEAD_TOKENS + int(len(text) / CHARS_PER_TOKEN)
    max_tokens = max(MIN_OUTPUT_TOKENS, min(MAX_OUTPUT_TOKENS, REQUEST_TOKEN_CEILING - est_input))
    structured = _make_llm(max_tokens).with_structured_output(CompactSpec, method="json_schema", strict=False, include_raw=True)
    out = _invoke_with_backoff(structured, _PROMPT + text)
    parsed = out.get("parsed") if isinstance(out, dict) else out
    if parsed is not None:
        return parsed
    print(f"[requirement_extractor] schema parse failed ({out.get('parsing_error')}) - recovering from raw output")
    return _recover_compact(out["raw"])


def extract_spec(requirement_text):
    notes = []
    text = requirement_text.strip()
    max_chars = int((REQUEST_TOKEN_CEILING - FIXED_OVERHEAD_TOKENS - MIN_OUTPUT_TOKENS) * CHARS_PER_TOKEN)
    if len(text) > max_chars:
        notes.append(f"Your text is {len(text):,} characters but only the first {max_chars:,} fit in one read - "
                     f"the rest was ignored. Generate a few sections at a time instead.")
        text = text[:max_chars]

    compact = _call_compact(text)
    period_len = compact.period_length_minutes if compact.period_length_minutes > 0 else 50
    sessions, parse_notes = _parse_rows(compact.rows, period_len)
    notes.extend(parse_notes)
    sessions = _resolve_section_labels(sessions, notes)

    for section in _incomplete_sections(sessions):
        sessions = _repair_section(text, section, sessions, period_len, notes)

    sizes = _section_sizes(sessions)
    if sizes:
        largest = max(len(v) for v in sizes.values())
        for section in _incomplete_sections(sessions):
            notes.append(f"Section {section} has only {len(sizes[section])} subjects while another has {largest} - "
                         f"check that your text for Section {section} lists everything.")

    times = list(dict.fromkeys(t for t in (_norm_time(x) for x in compact.period_start_times) if t))
    days = list(dict.fromkeys(_DAYS.get(d.strip()[:3].lower(), d.strip().title()) for d in compact.working_days if d.strip()))
    spec = GenerationSpec(
        title=compact.title or "Generated timetable", working_days=days,
        day_structure=DayStructure(period_start_times=times, period_length_minutes=period_len, lunch_after_period_index=None),
        sessions=sessions,
    )
    print(f"[requirement_extractor] title={spec.title!r} days={days} sessions={len(sessions)} "
          f"sections={sorted(sizes)} periods={len(times)} notes={len(notes)}")
    return spec, notes