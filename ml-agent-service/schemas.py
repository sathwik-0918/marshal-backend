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
    timetable_spec: Optional[dict] = None
    timetable_constraints: list = Field(default_factory=list)


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
    date: str = Field("", description="YYYY-MM-DD if stated, else empty - never guess")
    time: str = Field("", description="HH:MM if stated, else empty - never guess")
    duration_minutes: Optional[int] = None
    venue: str = ""
    description: str = ""
    participant_references: list[str] = Field(default_factory=list, description="Names/emails mentioned, as written")
    required_resources: list[str] = Field(default_factory=list)
    confidence: str = Field("high", description="'low' if inferred, not stated directly")


class ExtractedReferenceEntry(BaseModel):
    title: str
    entry_type: str = Field(description="recurring_weekly or date_range")
    description: str = ""
    weekday: Optional[str] = Field(None, description="recurring_weekly only")
    start_time: Optional[str] = Field(None, description="HH:MM, recurring_weekly only")
    end_time: Optional[str] = None
    start_date: Optional[str] = Field(None, description="YYYY-MM-DD, date_range only")
    end_date: Optional[str] = None
    venue: str = ""
    metadata: list[str] = Field(default_factory=list, description="'label: value' strings")


class ScheduleExtraction(BaseModel):
    detected_schedule_type: str = Field(description="What this document is, inferred from content")
    activities: list[ExtractedActivityRow] = Field(default_factory=list)
    reference_entries: list[ExtractedReferenceEntry] = Field(default_factory=list)
    additional_notes: str = Field("", description="Rules/policies, not activities")
    org_name: str = ""
    department: str = ""
    academic_term: str = ""
    location: str = ""
    
class SessionRequirement(BaseModel):
    subject_name: str
    session_kind: str = Field(description="'class', 'lab', or 'special'")
    duration_minutes: int
    weekly_count: int
    faculty: list[str] = Field(default_factory=list)
    max_same_day_minutes: Optional[int] = Field(None, description="Cap on same-subject minutes per day, if explicitly stated")
    section: str = Field("", description="Section/group this belongs to - for future multi-section generation")
    venue: str = Field("", description="Room or lab this session needs, if explicitly stated (e.g. 'Lab 13,14') - empty if not stated")


class DayStructure(BaseModel):
    period_start_times: list[str] = Field(description="HH:MM start of each period, in order")
    period_length_minutes: int
    lunch_after_period_index: Optional[int] = Field(None, description="0-indexed - lunch falls after this period index")


class GenerationSpec(BaseModel):
    title: str
    working_days: list[str] = Field(description="e.g. ['Monday', 'Tuesday', ..., 'Saturday']")
    day_structure: DayStructure
    sessions: list[SessionRequirement]
    
class FeedbackConstraint(BaseModel):
    constraint_type: str = Field(description="separate_days | avoid_day | require_day | time_preference | faculty_unavailable | unsupported")
    subjects: list[str] = Field(default_factory=list)
    faculty: list[str] = Field(default_factory=list)
    section: Optional[str] = Field(None, description="Limit to one section, if the feedback named one")
    day: Optional[str] = None
    part_of_day: Optional[str] = Field(None, description="morning or afternoon - for faculty_unavailable")
    time_preference: Optional[str] = None
    is_hard: bool = True
    one_off: bool = Field(False, description="True if about one specific date (today, tomorrow, this Friday, a calendar date), not a standing change")

class FeedbackExtraction(BaseModel):
    constraints: list[FeedbackConstraint] = Field(default_factory=list)
    unrecognized: str = Field("", description="Feedback that didn't map to a supported constraint type - never silently dropped")
    
