import os
from langchain_groq import ChatGroq
from schemas import FeedbackExtraction

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
_llm = ChatGroq(api_key=GROQ_API_KEY, model="openai/gpt-oss-120b", temperature=0, max_tokens=1500, reasoning_effort="low", max_retries=3)

_PROMPT = (
    "The user is changing a weekly class timetable. Extract each distinct request as a "
    "structured constraint.\n\n"
    "constraint_type - exactly one of:\n"
    "  separate_days       - two or more subjects must not fall on the same day\n"
    "  avoid_day           - a subject must not be on a specific day\n"
    "  require_day         - a subject must be on a given day\n"
    "  time_preference     - a subject should lean toward morning or afternoon\n"
    "  faculty_unavailable - a teacher cannot teach on a day, or in the morning/afternoon "
    "(absent, on leave, unavailable, can't come). Put the teacher in `faculty`, the weekday "
    "in `day` (leave empty if it applies to every day), and morning/afternoon in "
    "`part_of_day` if stated.\n"
    "  unsupported         - anything else; never silently drop it\n\n"
    "is_hard: true for a firm requirement ('can't', 'never', 'must', 'absent'), false for a "
    "preference ('prefer', 'ideally').\n"
    "one_off: true ONLY if the request is about one specific date (today, tomorrow, this "
    "Friday, 12 October). A plain weekday ('on Tuesdays') or 'from now on' is a standing "
    "change, so false.\n\n"
    "Use ONLY names from these lists - match even if the user leaves out titles or initials, "
    "and never invent one.\n"
    "Known subjects: {subjects}\nKnown teachers: {faculty}\nKnown sections: {sections}\n\n"
    "Message: \"{feedback}\""
)


def extract_feedback(feedback_text, known_subjects, known_faculty=None, known_sections=None):
    structured_llm = _llm.with_structured_output(FeedbackExtraction, method="json_schema", strict=False)
    result = structured_llm.invoke(_PROMPT.format(
        subjects=", ".join(known_subjects) or "(none)",
        faculty=", ".join(known_faculty or []) or "(none)",
        sections=", ".join(known_sections or []) or "(none)",
        feedback=feedback_text.strip()[:2000],
    ))
    print(f"[feedback_extractor] {len(result.constraints)} constraint(s), unrecognized={result.unrecognized!r}")
    return result