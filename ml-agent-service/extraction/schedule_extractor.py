import os
from langchain_groq import ChatGroq
from schemas import ScheduleExtraction

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# Reduced from 4096 - that reservation alone was consuming over half this
# account's 8000 TPM ceiling on every call, before content was even
# counted. Chunking below means no single call needs to hold a whole
# large document's output anyway.
_extraction_llm = ChatGroq(api_key=GROQ_API_KEY, model="openai/gpt-oss-120b", temperature=0, max_tokens=3000, reasoning_effort="low")

CHUNK_CHAR_SIZE = 9000  # leaves comfortable margin under the 8000 TPM ceiling once rules+schema+reserved-output are accounted for
MAX_CHUNKS = 15  # safety valve against a pathological upload, not a realistic ceiling - 80k chars is ~9 chunks

_PROMPT_INTRO = (
    "Read this document and extract every distinct, schedulable activity it describes - "
    "a match, class, meal, ceremony, session, exam, or similar. Capture each one's date, "
    "time, duration, venue, and anyone named as involved.\n\n"
)

_RULES = (
    "Rules:\n"
    "- Only extract what the document actually states. Never invent a date, time, venue, "
    "or person not written down.\n"
    "- If a field's value is literally the word UNDEFINED in the source, treat it as "
    "genuinely unknown - output an empty value for that field, never the literal word.\n"
    "- Every date must be written as exactly YYYY-MM-DD. Never 'June 29, 2026', "
    "'29-06-2026', or any other format.\n"
    "- If the document uses relative day labels like 'Day 1' AND states a real calendar "
    "date somewhere, resolve the labels against it. If it gives ONLY relative labels with "
    "no real date anywhere, leave date empty rather than guessing one.\n"
    "- If a time is missing, leave it empty and mark confidence low - never guess.\n"
    "- If the document defines a legend mapping a short code to real clock times (for "
    "example 'FN = 10:00 AM to 12:00 PM'), resolve every row referencing that code into "
    "its real start time, and compute duration_minutes from the legend's start/end.\n"
    "- If a date or date range is stated ONCE as a heading applying to several rows below "
    "or beside it, apply that same date to every one of those rows - for reference "
    "entries just as much as activities.\n"
    "- If the document is a grid where rows are branches/sections and columns are "
    "dates/sessions (exam timetables), and the SAME subject appears for MULTIPLE branches "
    "at the exact same date and session (sometimes marked 'Common to' a list of branches), "
    "combine them into ONE activity, not one per branch. Title it exactly "
    "'<Subject> (<Branch1>, <Branch2>, ...)' listing every branch that shares it. If a "
    "subject appears for only one branch, title it '<Subject> (<Branch>)'. ALWAYS put the "
    "branch(es) directly in the title this way, never only in description.\n"
    "- A name immediately followed by an administrative title - Controller of "
    "Examinations, Principal, Director, HOD, Registrar, Chairman, Coordinator - is "
    "whoever issued the document. NEVER extract them as a participant or resource.\n"
    "- Capture participant references exactly as written. Never invent an email for "
    "someone only named, not emailed.\n"
    "- This document may contain TWO kinds of schedulable content:\n"
    "  ACTIVITY: one specific calendar date and clock time. Put in `activities`.\n"
    "  REFERENCE ENTRY: no single date+time - either (a) repeats weekly on a weekday, "
    "entry_type='recurring_weekly' with weekday + optional start_time/end_time, OR (b) "
    "spans a date range, entry_type='date_range' with start_date + end_date. Put in "
    "`reference_entries`.\n"
    "- For a WEEKLY MENU specifically: put the actual food items for each meal into "
    "metadata as one entry starting with exactly 'menu:' then items comma-separated.\n"
    "- For any other reference entry, put names/codes/sections/cohorts/faculty into "
    "`metadata` as short 'label: value' strings.\n"
    "- Rules, policies, or instructions that are NOT a schedulable activity go into "
    "additional_notes.\n"
    "- Separately, if the document identifies its organization, extract: org_name, "
    "department, academic_term, location. Leave empty if not stated.\n"
    "- Never return an empty activities list and an empty reference_entries list if the "
    "document describes real content, even with low confidence.\n"
)


def _split_into_chunks(text: str, chunk_size: int = CHUNK_CHAR_SIZE) -> list:
    """Splits on the nearest newline at or before chunk_size, so a chunk
    boundary doesn't land mid-row. One real, known limitation: if a date
    heading and the rows it applies to happen to straddle a chunk
    boundary, that specific relationship can be missed - narrow, and
    worth knowing rather than hiding."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > chunk_size:
        cut = remaining.rfind('\n', 0, chunk_size)
        if cut <= 0:
            cut = chunk_size
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip('\n')
    if remaining:
        chunks.append(remaining)
    if len(chunks) > MAX_CHUNKS:
        print(f"[schedule_extractor] {len(chunks)} chunks exceeds the {MAX_CHUNKS} safety cap, truncating")
        chunks = chunks[:MAX_CHUNKS]
    return chunks


def _extract_single_chunk(text: str) -> ScheduleExtraction:
    structured_llm = _extraction_llm.with_structured_output(ScheduleExtraction, method="json_schema", strict=False)
    return structured_llm.invoke(_PROMPT_INTRO + _RULES + f"\nDocument:\n{text}")


def _merge_extractions(results: list) -> ScheduleExtraction:
    all_activities, all_references, all_notes = [], [], []
    detected_type = org_name = department = academic_term = location = ""
    for r in results:
        all_activities.extend(r.activities)
        all_references.extend(r.reference_entries)
        if r.additional_notes:
            all_notes.append(r.additional_notes)
        # First non-empty wins - org/context info typically only appears
        # once, near the top of the document, in the first chunk.
        detected_type = detected_type or r.detected_schedule_type
        org_name = org_name or r.org_name
        department = department or r.department
        academic_term = academic_term or r.academic_term
        location = location or r.location
    return ScheduleExtraction(
        detected_schedule_type=detected_type, activities=all_activities, reference_entries=all_references,
        additional_notes="\n".join(all_notes), org_name=org_name, department=department,
        academic_term=academic_term, location=location,
    )


def extract_from_text(raw_text: str) -> ScheduleExtraction:
    text = raw_text.strip()
    chunks = _split_into_chunks(text)

    if len(chunks) == 1:
        result = _extract_single_chunk(chunks[0])
        print(f"[schedule_extractor] type={result.detected_schedule_type!r} "
              f"activities={len(result.activities)} references={len(result.reference_entries)}")
        return result

    print(f"[schedule_extractor] {len(text)} chars - splitting into {len(chunks)} chunks to stay under the account's token limit")
    results = []
    for i, chunk in enumerate(chunks):
        try:
            results.append(_extract_single_chunk(chunk))
            print(f"[schedule_extractor] chunk {i + 1}/{len(chunks)}: activities={len(results[-1].activities)} references={len(results[-1].reference_entries)}")
        except Exception as error:
            print(f"[schedule_extractor] chunk {i + 1}/{len(chunks)} failed ({error}) - skipping")
    if not results:
        raise RuntimeError("Every chunk failed to extract")

    merged = _merge_extractions(results)
    print(f"[schedule_extractor] merged {len(results)} chunk(s): activities={len(merged.activities)} references={len(merged.reference_entries)}")
    return merged


def extract_from_pdf(pdf_bytes: bytes, filename: str = "document.pdf") -> ScheduleExtraction:
    from knowledge import pdf_utils
    return extract_from_text(pdf_utils.extract_text(pdf_bytes, filename))