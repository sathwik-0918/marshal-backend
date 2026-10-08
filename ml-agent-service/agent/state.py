from typing import TypedDict, Optional, Callable


class AgentState(TypedDict):
    schedule_id: str
    schedule_name: str
    activities: list
    reference_entries: list
    raw_message: str
    intent: str

    understood: Optional[dict]
    relevant_activities: list

    rewritten_query: Optional[str]
    documents: list
    sources: list
    generation_count: int
    grade_passed: bool

    ml_predictions: dict

    proposal: Optional[dict]
    validated: bool

    timetable_spec: Optional[dict]
    timetable_constraints: list

    emit_progress: Optional[Callable]