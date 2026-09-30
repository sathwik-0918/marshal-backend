from pydantic import BaseModel, Field
from typing import Optional


class ActivityContext(BaseModel):
    id: str = Field(alias="_id")
    title: str
    venue: str
    scheduledStart: str
    durationMinutes: int
    activityType: Optional[str] = "session"
    status: Optional[str] = "scheduled"
    stakeholders: list = []
    requiredResources: list[str] = []
    dependencies: list[str] = []

    class Config:
        populate_by_name = True


class AgentRequest(BaseModel):
    schedule_id: str
    schedule_name: str
    message: str
    activities: list[ActivityContext]


class IntentExtraction(BaseModel):
    named_activities: list[str] = Field(default_factory=list, description="Exact activity titles the message states")
    mentioned_venues: list[str] = Field(default_factory=list, description="Venue names the message itself states")
    mentioned_resources: list[str] = Field(default_factory=list, description="Person or resource names the message itself states")
    activity_type_keywords: list[str] = Field(default_factory=list, description="Types mapped from a generic category word like 'games' or 'classes'")
    time_window_start: Optional[str] = Field(None, description="LOCAL ISO 8601, no timezone letter or offset")
    time_window_end: Optional[str] = Field(None, description="LOCAL ISO 8601, no timezone letter or offset")
    operation: str = Field("unclear", description="delay | advance | unavailable | move_venue | move_time | cancel | replan | unclear")
    shift_minutes: Optional[int] = Field(None, description="Minutes, for delay or advance only")
    target_venue: Optional[str] = None
    target_time_of_day: Optional[str] = Field(None, description="HH:MM 24-hour")
    target_date: Optional[str] = Field(None, description="YYYY-MM-DD, only if the message names a date")
    summary: str = ""
    needs_knowledge: bool = False


class RuleNote(BaseModel):
    option_index: int
    note: str


class RuleNotes(BaseModel):
    notes: list[RuleNote] = Field(default_factory=list)


class AgentResponse(BaseModel):
    # Deliberately plain dicts. A typed response model silently deletes every key it doesn't declare.
    # That is exactly how the validation warnings were being erased before they reached the UI.
    understood: dict
    proposal: dict