import os
from langchain_groq import ChatGroq
from schemas import ScheduleExtraction

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Deliberately larger than any other structured call in this codebase -
# a real document can describe dozens of activities. Budgeted generously
# up front rather than waiting to hit the same truncation failure mode
# this project has already debugged more than once.
_extraction_llm = ChatGroq(api_key=GROQ_API_KEY, model="openai/gpt-oss-120b", temperature=0, max_tokens=4096, reasoning_effort="low")

MAX_TEXT_CHARS = 20000  # a dense multi-page timetable or calendar can exceed the old limit


def extract(raw_text: str) -> ScheduleExtraction:
    stripped = raw_text.strip()
    if len(stripped) > MAX_TEXT_CHARS:
        print(f"[schedule_extractor] document truncated: {len(stripped)} chars -> {MAX_TEXT_CHARS}")
    text = stripped[:MAX_TEXT_CHARS]
    structured_llm = _extraction_llm.with_structured_output(ScheduleExtraction, method="json_schema", strict=False)

    prompt = (
        "Read this document and extract every distinct, schedulable activity it describes - "
        "a match, class, meal, ceremony, session, or similar. Capture each one's date, time, "
        "duration, venue, and anyone named as involved.\n\n"
        "Rules:\n"
        "- Only extract what the document actually states. Never invent a date, time, venue, "
        "or person not written down.\n"
        "- If the document uses relative day labels like 'Day 1' AND states a real calendar "
        "date somewhere, resolve the labels against it. If it gives ONLY relative labels with "
        "no real date anywhere, leave date empty for those rows rather than guessing one.\n"
        "- If a time is missing, leave it empty and mark confidence low - never guess a "
        "plausible-sounding time.\n"
        "- Capture participant references exactly as written - a name, a team, or an email if "
        "one is given. Never invent an email for someone only named, not emailed.\n"
        "- If a date or day appears ONCE as a heading or section title, followed by several rows that "
        "don't repeat it individually, apply that heading's date to every one of those rows until a new "
        "date heading appears. A date stated once above a block of activities still applies to all of them - "
        "don't leave date empty just because it wasn't repeated on every single row.\n"
        "- Never extract a name that only appears in a signature block, letterhead, or distribution/'copy to' "
        "list as a participant or resource - that's who issued the document, not who's running an activity in it. "
        "Only extract a name as a participant/resource if they're clearly tied to a specific listed activity.\n"
        "- Rules, policies, or instructions that are NOT themselves a schedulable activity "
        "(e.g. 'Ground 3 cannot be used after 4 PM', 'Professor Ravi is unavailable Monday "
        "morning') go into additional_notes, never into an activity row.\n"
        "- Label the schedule type based on what the document actually contains - don't "
        "default to 'sports event' just because that's a common example.\n"
        "- If the document defines a legend mapping a short code to real clock times (for example "
        "'FN = 10:00 AM to 12:00 PM', 'AN = 2:00 PM to 4:00 PM'), resolve every row referencing that "
        "code into its real start time, and compute duration_minutes from the legend's stated start "
        "and end. Do not leave time or duration empty just because a row only shows the short code - "
        "look up its definition elsewhere in the document.\n"
        "- If the document is a grid where rows are branches/sections and columns are dates or "
        "sessions (common in exam timetables), each branch-row produces one activity per date/session "
        "it has an entry for. The branch name describes WHICH group the activity is for (put it in "
        "title or description) - it is never a participant or resource.\n"
        "- A name immediately followed by an administrative title - Controller of Examinations, "
        "Principal, Director, HOD, Registrar, Chairman, Coordinator, or similar - is whoever issued "
        "or authorized the document. NEVER extract them as a participant or resource, regardless of "
        "where in the extracted text that name appears - PDF extraction can place a signature next "
        "to unrelated content. Only extract a name as a participant/resource when the document ties "
        "them directly to one specific activity (e.g. 'invigilator: X' beside that exam).\n"
        "- Never return an empty activities list if the document describes real individually-occurring "
        "activities, even with low confidence. Include a row with whatever you can determine, mark its "
        "confidence low, and leave only the genuinely unknown fields blank - an incomplete row a human "
        "can finish is far more useful than no row at all. A document containing only reference entries "
        "may have an empty activities list.\n"
        "- This document may contain TWO different kinds of schedulable content, and you must choose "
        "correctly for each item:\n"
        "  ACTIVITY: a specific, individually-occurring event with its own single calendar date and "
        "clock time - a match, an exam session, a ceremony. Put these in `activities`.\n"
        "  REFERENCE ENTRY: content with NO single date+time, instead either (a) repeating every week "
        "on a weekday with no specific date - a class timetable slot, a weekly menu item - use "
        "entry_type='recurring_weekly' with weekday + optional start_time/end_time, OR (b) spanning a "
        "range of calendar dates rather than one moment - a term phase, an exam prep period, a "
        "multi-day training block - use entry_type='date_range' with start_date + end_date. Put these "
        "in `reference_entries`, never `activities`.\n"
        "- Never force weekly-recurring or date-range content into an activity by inventing a single "
        "fake date or a fabricated duration - use reference_entries instead.\n"
        "- For a reference entry, put any names/codes/sections/cohorts/faculty mentioned into "
        "`metadata` as short 'label: value' strings (e.g. 'faculty: Mrs. M. Parvathi', 'cohort: "
        "CSM-2') - these describe who the entry is FOR or ABOUT, never an individual person/resource "
        "to treat as exclusively occupied.\n"
        "- If you can identify what the document is and its general structure but genuinely cannot "
        "extract confident activity or reference-entry rows, describe what you found in additional_notes "
        "rather than leaving activities, reference_entries and additional_notes empty.\n\n"
        f"Document:\n{text}"
    )
    result = structured_llm.invoke(prompt)
    print(f"[schedule_extractor] type={result.detected_schedule_type!r} activities={len(result.activities)} notes_len={len(result.additional_notes)}")
    return result
