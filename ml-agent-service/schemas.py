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


class ReferenceEntryContext(BaseModel):
    id: str = Field(alias="_id")
    title: str
    entryType: str
    description: str = ""
    weekday: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    venue: str = ""
    metadata: list[str] = []

    class Config:
        populate_by_name = True


class AgentRequest(BaseModel):
    schedule_id: str
    schedule_name: str
    message: str
    activities: list[ActivityContext]
    reference_entries: list[ReferenceEntryContext] = Field(default_factory=list)


class IntentRoute(BaseModel):
    intent: str = Field(description="One of: general_chat, schedule_query, knowledge_query, schedule_action")


class QuerySpec(BaseModel):
    query_type: str = Field(description="count | list | lookup_time | lookup_venue | lookup_participants | lookup_resources | exists | total_duration | summary | unsupported")
    activity_name: Optional[str] = Field(None, description="A specific activity title or a fragment of one, ONLY if one is actually named")
    day_number: Optional[int] = Field(None, description="The N in 'Day N', if referenced")
    weekday: Optional[str] = Field(None, description="Day of week if referenced directly, e.g. 'Monday' - different from day_number, which means 'Day N' of a multi-day event")
    venue: Optional[str] = Field(None, description="A specific venue, ONLY if the message names one directly")
    activity_type_keywords: list[str] = Field(default_factory=list, description="Types mapped from a category word against the schedule's real vocabulary")


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
    understood: dict = Field(default_factory=dict)
    proposal: dict
    
class ExtractedActivityRow(BaseModel):
    title: str
    activity_type: str = "session"
    date: str = Field("", description="YYYY-MM-DD, resolved from a real calendar date stated in the document. Empty if none is ever stated - never guessed.")
    time: str = Field("", description="HH:MM 24-hour, only if explicitly stated. Empty if not given - never guessed.")
    duration_minutes: Optional[int] = None
    venue: str = ""
    description: str = ""
    participant_references: list[str] = Field(default_factory=list, description="Names or emails of people/teams mentioned for this activity, exactly as written")
    required_resources: list[str] = Field(default_factory=list)
    confidence: str = Field("high", description="'low' if any field had to be inferred rather than read directly")


class ExtractedReferenceEntry(BaseModel):
    title: str
    entry_type: str = Field(description="'recurring_weekly' or 'date_range'")
    description: str = ""
    weekday: Optional[str] = Field(None, description="'Monday' etc - only for recurring_weekly")
    start_time: Optional[str] = Field(None, description="HH:MM - only for recurring_weekly")
    end_time: Optional[str] = None
    start_date: Optional[str] = Field(None, description="YYYY-MM-DD - only for date_range")
    end_date: Optional[str] = None
    venue: str = ""
    metadata: list[str] = Field(default_factory=list, description="Short 'label: value' strings - faculty, section, cohort, subject code, etc.")


class ScheduleExtraction(BaseModel):
    detected_schedule_type: str = Field(description="A short label for what this document actually is, e.g. 'Sports event', 'College timetable', 'Hostel mess menu', 'Other' - inferred from content, not assumed")
    activities: list[ExtractedActivityRow] = Field(default_factory=list)
    reference_entries: list[ExtractedReferenceEntry] = Field(default_factory=list)
    additional_notes: str = Field("", description="Rules/policies/instructions that are NOT a schedulable activity - never put this content into an activity row")