from pydantic import BaseModel, Field
from typing import Optional


class ActivityContext(BaseModel):
    id: str = Field(alias="_id")
    title: str
    venue: str
    scheduledStart: str
    durationMinutes: int
    activityType: Optional[str] = "session"
    stakeholders: list = []

    class Config:
        populate_by_name = True


class AgentRequest(BaseModel):
    schedule_id: str
    schedule_name: str
    message: str
    activities: list[ActivityContext]


class UnderstoodRequest(BaseModel):
    """Structured output schema for the understanding node."""
    activity_id: Optional[str] = Field(None, description="The _id of the activity this message is about, exactly as given in the activity list. Null if none can be determined.")
    issue_type: str = Field(description="One of: delay, cancellation, venue_conflict, reschedule_request, other")
    summary: str = Field(description="One-sentence plain-English summary of what's being reported")
    minutes_mentioned: Optional[int] = Field(None, description="Any specific delay/duration in minutes mentioned in the message")
    needs_knowledge: bool = Field(description="True if answering this properly requires checking uploaded schedule documents, policies, or venue rules — not just the live schedule and ML predictions")


class ProposalOption(BaseModel):
    description: str = Field(description="Plain-English description of this proposed change")
    new_venue: Optional[str] = None
    new_start_time: Optional[str] = Field(None, description="ISO 8601 datetime if this option changes the start time")


class ProposalSet(BaseModel):
    """Structured output schema for the proposal-generation node."""
    needs_clarification: bool
    clarification_question: Optional[str] = None
    risk_tier: str = Field(description="low, medium, or high")
    options: list[ProposalOption] = Field(default_factory=list)


class AgentResponse(BaseModel):
    understood: UnderstoodRequest
    proposal: ProposalSet