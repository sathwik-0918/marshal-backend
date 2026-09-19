from typing import TypedDict, Optional, Callable


class AgentState(TypedDict):
    schedule_id: str
    schedule_name: str
    activities: list
    raw_message: str

    understood: Optional[dict]
    relevant_activity: Optional[dict]

    rewritten_query: Optional[str]
    documents: list
    sources: list
    generation_count: int
    grade_passed: bool

    ml_prediction: Optional[dict]

    proposal: Optional[dict]
    validated: bool

    emit_progress: Optional[Callable]