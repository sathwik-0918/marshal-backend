from typing import TypedDict, Optional, Callable


class AgentState(TypedDict):
    schedule_id: str
    schedule_name: str
    activities: list
    raw_message: str

    understood: Optional[dict]
    relevant_activities: list  # was relevant_activity (singular)

    rewritten_query: Optional[str]
    documents: list
    sources: list
    generation_count: int
    grade_passed: bool

    ml_predictions: dict  # was ml_prediction (singular) — now keyed by activity_id

    proposal: Optional[dict]
    validated: bool

    emit_progress: Optional[Callable]