import os
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from agent.state import AgentState
from agent.scope_resolver import resolve_affected_activities
from agent.time_utils import parse_datetime, local_date, local_now, SCHEDULE_TZ, fmt_local
from agent import constraints as C
from agent import planner as P
from schemas import IntentExtraction, IntentRoute, QuerySpec, RuleNotes
from ml import predictor
from knowledge import store_manager
from agent import query_engine

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# One model, two jobs: read the request, and (optionally) annotate finished options with rule notes.
# It never writes a time or a venue.
fast_llm = ChatGroq(api_key=GROQ_API_KEY, model="openai/gpt-oss-120b", temperature=0, max_tokens=1536, reasoning_effort="low")


def _emit(state: AgentState, node: str, status: str, detail: str, data=None):
    callback = state.get("emit_progress")
    if not callback:
        return
    try:
        callback({"node": node, "status": status, "detail": detail, "data": data or {}})
    except Exception as error:
        print(f"[TRACE EMIT ERROR] {error}")


def _clarify(question: str) -> dict:
    return {"proposal": {"needs_clarification": True, "clarification_question": question,
                         "risk_tier": "low", "options": []}}


def _dates_context(activities: list) -> str:
    dates = set()
    for a in activities:
        try:
            dates.add(local_date(parse_datetime(a["scheduledStart"])))
        except (ValueError, KeyError, TypeError):
            continue
    return "\n".join(f"Day {i + 1} = {d.isoformat()} ({d.strftime('%A')})" for i, d in enumerate(sorted(dates)))


def _type_vocabulary(activities: list) -> list:
    return sorted({(a.get("activityType") or "").strip().lower() for a in activities if (a.get("activityType") or "").strip()})


_INTENT_GUIDE = """You are extracting the user's INTENT as structured fields. You know nothing about any activity's venue, people or exact time, and must not guess them - only transcribe what the message states.

Fields:
- named_activities: exact activity titles the message states.
- mentioned_venues / mentioned_resources: venue names and person or resource names the message states, exactly as written.
- activity_type_keywords: ONLY when the message uses a generic category word (games, matches, classes, lectures, meals) and names no specific activity, venue or person. Map it to the matching types from the list above. Otherwise leave empty.
- time_window_start / time_window_end: the affected period as LOCAL wall-clock ISO 8601 with NO timezone letter or offset, for example 2026-10-05T08:30:00. "Day N" means the Nth date listed above. Relative words (tomorrow, Monday) are relative to today. When no hours are given use: morning 06:00-12:00, afternoon 12:00-17:00, evening 17:00-21:00, whole day 00:00-23:59. Leave both null if the message gives no time scope.
- operation: exactly one of
    delay       (running late or pushed later by N minutes)
    advance     (brought earlier by N minutes)
    unavailable (a venue or person cannot be used during the window)
    move_venue  (move to a named venue)
    move_time   (move to a stated start time)
    cancel
    replan      (asks generally to replan, reschedule or find alternatives)
    unclear     (only if you truly cannot tell)
- shift_minutes: minutes, for delay or advance only.
- target_venue: for move_venue, the destination venue.
- target_time_of_day: HH:MM in 24-hour time, for move_time. target_date: YYYY-MM-DD, only if the message names a date for the new start.
- needs_knowledge: true only if answering needs uploaded documents such as rules or policies.

Examples:
"Ground 3 is unavailable from 8:30 to 11:30 on October 5" -> operation unavailable, mentioned_venues Ground 3, window 2026-10-05T08:30:00 to 2026-10-05T11:30:00.
"Professor Ravi is out Monday morning" -> operation unavailable, mentioned_resources Professor Ravi, window = that Monday 06:00 to 12:00.
"Day 1 morning games are delayed 45 minutes" -> operation delay, shift_minutes 45, activity_type_keywords = the game types from the list, window = Day 1 06:00 to 12:00.
"Day 1 Evening Final is running 20 minutes late" -> operation delay, shift_minutes 20, named_activities Day 1 Evening Final, everything else empty.
"Move Volleyball Match A to Ground 2" -> operation move_venue, named_activities Volleyball Match A, target_venue Ground 2.
"""

_QUERY_GUIDE = """Convert this question about a schedule into a structured query.

query_type - exactly one of:
    count               how many activities match
    list                show/list the matching activities
    lookup_time         what time/when is a specific activity
    lookup_venue        what venue is X at, OR what's happening at a given venue
    lookup_participants who is involved in a specific activity
    lookup_resources    what resources/people a specific activity requires
    exists              is a specific activity on the schedule
    total_duration      sum of durations for the matching activities
    summary             general \"tell me about X\" for one specific activity
    unsupported         not answerable from structured schedule data alone

activity_name: a specific activity title or a fragment of one, ONLY if one is actually named
day_number: the N in \"Day N\", if referenced.
weekday: the day of week (Monday, Tuesday, etc.) if referenced directly - e.g. \"what's for dinner Wednesday\", \"what's on Monday\". Different from day_number, which means \"Day N\" of a multi-day event.
venue: a specific venue, ONLY if the message names one directly (not inferred from an activity).
activity_type_keywords: if the message uses a category word (games, matches, classes, lectures, meals) rather than naming one specific activity, map it to every matching type from this schedule's actual types: {types}. Leave empty if a specific activity was already named instead.
"""

def route_intent(state: AgentState) -> dict:
    _emit(state, "route_intent", "active", "Reading your message")
    msg = state["raw_message"].strip()

    # Cheap, deterministic pre-filter for the most obvious cases - saves
    # a full LLM round-trip for "hi" and skips straight past any risk of
    # a short greeting being misread as a scheduling question.
    if len(msg.split()) <= 3 and msg.lower().rstrip("!.") in (
        "hi", "hello", "hey", "hi marshal", "hello marshal", "thanks", "thank you", "ok", "okay"
    ):
        return {"intent": "general_chat"}

    try:
        structured_llm = fast_llm.with_structured_output(IntentRoute, method="json_schema", strict=False)
        prompt = (
            "Classify this message about a schedule into exactly one category:\n"
            "- general_chat: greetings, thanks, or asking what MARSHAL can do - no schedule data needed\n"
            "- schedule_query: asking ABOUT existing schedule facts - counts, times, venues, participants, "
            "'what's on Day N', 'is X scheduled', 'show all activities'. Answerable from structured data alone.\n"
            "- knowledge_query: asking about RULES, POLICIES, or anything from an uploaded document - "
            "'what are the rules', 'what does the document say about X', restrictions, requirements.\n"
            "- schedule_action: reporting a disruption or asking for something to be CHANGED - a delay, "
            "unavailability, a move, a cancellation. Anything that implies the schedule itself should change.\n\n"
            f"Message: \"{msg}\""
        )
        result = structured_llm.invoke(prompt)
        intent = result.intent if result.intent in ("general_chat", "schedule_query", "knowledge_query", "schedule_action") else "schedule_action"
    except Exception as error:
        print(f"[route_intent] failed, defaulting to schedule_action: {error}")
        intent = "schedule_action"  # the safest default is the path with the most existing guardrails, not the most permissive one

    _emit(state, "route_intent", "done", f"Routed as {intent}")
    return {"intent": intent}


def answer_general_chat(state: AgentState) -> dict:
    response = fast_llm.invoke([
        SystemMessage(content="You are MARSHAL, a schedule management assistant. Reply briefly and warmly - "
                               "a sentence or two. If asked what you can do, mention you can answer questions "
                               "about this schedule, look up rules from uploaded documents, or help replan "
                               "around a delay or conflict."),
        HumanMessage(content=state["raw_message"]),
    ])
    return {"proposal": {"needs_clarification": True, "clarification_question": response.content.strip(),
                         "risk_tier": "low", "options": [], "is_chat_reply": True}}


def answer_schedule_query(state: AgentState) -> dict:
    _emit(state, "answer_schedule_query", "active", "Checking the schedule")
    vocabulary = _type_vocabulary(state["activities"])
    try:
        structured_llm = fast_llm.with_structured_output(QuerySpec, method="json_schema", strict=False)
        prompt = _QUERY_GUIDE.format(types=", ".join(vocabulary) or "(none recorded)") + f"\nQuestion: \"{state['raw_message']}\""
        spec = structured_llm.invoke(prompt)
    except Exception as error:
        print(f"[answer_schedule_query] extraction failed: {error}")
        spec = QuerySpec(query_type="unsupported")

    result = query_engine.execute(spec, state["activities"], state.get("reference_entries", []))

    # Proceeding to the gap flagged last turn: "summary" is the one
    # query type worth also checking this schedule's documents - "tell
    # me about X" naturally includes any rule specific to X. Deliberately
    # NOT the full retrieve/grade/rewrite loop knowledge_query uses -
    # this is a small, deterministic enrichment on an already-answered
    # question, not a second independent one. A chunk is only attached
    # if the activity's own title, venue, or a required resource is a
    # literal match inside it - no LLM relevance judgment, so nothing
    # here can misattribute a document to the wrong activity.
    if spec.query_type == "summary" and len(result.get("matched_ids", [])) == 1:
        items = C.build_items(state["activities"])
        activity_item = items.get(result["matched_ids"][0])
        if activity_item:
            try:
                store = store_manager.get_store(state["schedule_id"])
                if store.index is not None and store.index.ntotal > 0:
                    hits = store.query(query_text=f"{activity_item.title} {activity_item.venue}", top_k=3)
                    needles = [activity_item.title.lower(), activity_item.venue.lower()] + [r.lower() for r in activity_item.resources]
                    for h in hits:
                        text = (h.get("metadata", {}) or {}).get("text", "")
                        if any(n and n in text.lower() for n in needles):
                            result["answer"] += " Related note from this schedule's documents: " + text[:300]
                            break
            except Exception as error:
                print(f"[answer_schedule_query] knowledge enrichment skipped: {error}")

    _emit(state, "answer_schedule_query", "done", "Answered")
    return {"proposal": {"needs_clarification": True, "clarification_question": result["answer"],
                         "risk_tier": "low", "options": [], "is_chat_reply": True}}


def answer_knowledge_query(state: AgentState) -> dict:
    """Reuses retrieve/grade exactly as they already exist - the gap
    ChatGPT correctly identified wasn't that RAG was broken, it's that
    nothing conversational was ever routed to it."""
    _emit(state, "answer_knowledge_query", "active", "Checking uploaded documents")
    documents = state.get("documents") or []
    sources = state.get("sources") or []
    if not documents:
        return {"proposal": {"needs_clarification": True,
                             "clarification_question": "I didn't find anything in this schedule's uploaded documents that answers that.",
                             "risk_tier": "low", "options": [], "is_chat_reply": True}}

    response = fast_llm.invoke([
        SystemMessage(content="Answer the question using ONLY the provided document excerpts. If they don't "
                               "actually answer it, say so plainly rather than guessing."),
        HumanMessage(content=f"Question: {state['raw_message']}\n\nDocument excerpts:\n" + "\n---\n".join(documents[:3])[:1800]),
    ])
    answer_text = response.content.strip()
    if sources:
        answer_text += f" (from: {', '.join(sources)})"
    return {"proposal": {"needs_clarification": True, "clarification_question": answer_text,
                         "risk_tier": "low", "options": [], "is_chat_reply": True}}

def understand_request(state: AgentState) -> dict:
    _emit(state, "understand_request", "active", "Reading the request")
    today = local_now()
    prompt = (
        f"Today is {today.strftime('%A')} {today.date().isoformat()}.\n"
        f"This schedule's dates, in order:\n{_dates_context(state['activities'])}\n"
        f"Activity types used on this schedule: {', '.join(_type_vocabulary(state['activities'])) or '(none recorded)'}\n\n"
        + _INTENT_GUIDE
        + f"\nMessage: \"{state['raw_message']}\""
    )
    failed = False
    try:
        structured_llm = fast_llm.with_structured_output(IntentExtraction, method="json_schema", strict=False)
        result = structured_llm.invoke(prompt)
    except Exception as error:
        print(f"[understand_request] extraction failed: {error}")
        result = IntentExtraction()
        failed = True

    affected_ids = resolve_affected_activities(state["activities"], result)
    relevant = [a for a in state["activities"] if a["_id"] in affected_ids]

    print(f"[understand_request] op={result.operation} shift={result.shift_minutes} named={result.named_activities} "
          f"venues={result.mentioned_venues} resources={result.mentioned_resources} "
          f"types={result.activity_type_keywords} window={result.time_window_start} to {result.time_window_end}")
    print(f"[understand_request] resolved to {len(affected_ids)} activities: {affected_ids}")

    understood = result.model_dump()
    understood["affected_activity_ids"] = affected_ids
    understood["extraction_failed"] = failed

    _emit(state, "understand_request", "done", "Request understood",
          {"affectedCount": len(relevant), "needsKnowledge": result.needs_knowledge})
    return {"understood": understood, "relevant_activities": relevant}


def retrieve(state: AgentState) -> dict:
    _emit(state, "retrieve", "active", "Searching this schedule's documents")
    query = state.get("rewritten_query") or state["raw_message"]
    store = store_manager.get_store(state["schedule_id"])
    if store.index is None or store.index.ntotal == 0:
        _emit(state, "retrieve", "done", "No documents uploaded for this schedule yet")
        return {"documents": [], "sources": []}
    results = store.query(query_text=query, top_k=5)
    documents, sources = [], []
    for r in results:
        meta = r.get("metadata", {})
        text = meta.get("text", "")
        source = meta.get("source", "Unknown")
        if text:
            documents.append(text)
            if source not in sources:
                sources.append(source)
    _emit(state, "retrieve", "done", f"Found {len(documents)} relevant sections", {"sources": sources})
    return {"documents": documents, "sources": sources}


def grade(state: AgentState) -> dict:
    documents = state["documents"]
    generation_count = state["generation_count"]
    _emit(state, "grade", "active", "Checking whether this evidence is relevant")
    if not documents:
        _emit(state, "grade", "done", "No evidence found", {"passed": False})
        return {"grade_passed": False, "generation_count": generation_count + 1}
    context_preview = "\n\n".join(documents[:5])[:2000]
    system_prompt = (
        "You are a relevance grader for event/schedule organizer documents. Decide whether the "
        "provided documents contain enough information to answer the request. Reply with exactly "
        "one word: yes or no."
    )
    response = fast_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Request: {state['raw_message'][:200]}\n\nDocs:\n{context_preview}"),
    ])
    passed = "yes" in response.content.strip().lower()
    _emit(state, "grade", "done", "Evidence checked", {"passed": passed})
    return {"grade_passed": passed, "generation_count": generation_count if passed else generation_count + 1}


def rewrite(state: AgentState) -> dict:
    _emit(state, "rewrite", "active", "Refining the search")
    system_prompt = (
        "Rewrite this request to be a more specific search query for event schedule documents - "
        "policies, venue rules, activity requirements. Return ONLY the rewritten query."
    )
    response = fast_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["raw_message"][:300]),
    ])
    rewritten = response.content.strip()[:300]
    _emit(state, "rewrite", "done", "Search refined", {"rewrittenQuery": rewritten})
    return {"rewritten_query": rewritten}


def get_ml_prediction(state: AgentState) -> dict:
    predictions = {}
    for activity in state.get("relevant_activities") or []:
        try:
            local = parse_datetime(activity["scheduledStart"]).astimezone(SCHEDULE_TZ)
            predictions[activity["_id"]] = predictor.predict(
                activity_type=activity.get("activityType") or "session",
                venue=activity["venue"],
                scheduled_duration=activity["durationMinutes"],
                num_people=len(activity.get("stakeholders", [])) or 10,
                time_of_day=local.hour,
                stakeholder_past_delay_rate=0.3,
                day_of_event=1,
                is_weekend=1 if local.weekday() >= 5 else 0,
            )
        except Exception as error:
            print(f"[get_ml_prediction] skipped {activity.get('title')}: {error}")
    return {"ml_predictions": predictions}


def plan_changes(state: AgentState) -> dict:
    _emit(state, "plan_changes", "active", "Working out valid options")
    understood = state.get("understood") or {}
    if understood.get("extraction_failed"):
        return _clarify("I had trouble reading that request. Please try rephrasing it, or send it again.")

    items = C.build_items(state["activities"])
    affected = [
        items[a["_id"]] for a in (state.get("relevant_activities") or [])
        if a["_id"] in items and items[a["_id"]].status not in ("cancelled", "completed")
    ]
    if not affected:
        return _clarify(P.no_match_question(understood, items))

    # A match found ONLY through a generic type word, with nothing else
    # narrowing it down, covering a large slice of the schedule - that's
    # what "the first three volleyball matches" (matched all 7, all 3
    # days) and "the match" (matched 20 activities) both looked like.
    # Neither had a name, venue, resource, or time window pinning it down.
    scoped_precisely = bool(
        understood.get("named_activities") or understood.get("mentioned_venues") or understood.get("mentioned_resources")
    )
    scoped_by_window = bool(understood.get("time_window_start") and understood.get("time_window_end"))
    if not scoped_precisely and not scoped_by_window and len(affected) > min(5, max(3, len(items) // 4)):
        names = ", ".join(a.title for a in affected[:5])
        more = f", and {len(affected) - 5} more" if len(affected) > 5 else ""
        return _clarify(
            f"That matched {len(affected)} activities ({names}{more}) - wider than I'd act on without checking. "
            f"Can you narrow it to a specific day, time, or name a few of them directly?"
        )

    intent = P.intent_from_understood(understood, items)
    question = P.missing_info(intent, affected)
    if question:
        return _clarify(question)

    options, unplaced = P.plan_options(intent, items, affected)
    if not options:
        return _clarify(P.failure_message(unplaced, items))

    _emit(state, "plan_changes", "done", f"{len(options)} option(s) ready")
    return {"proposal": {"needs_clarification": False, "clarification_question": None,
                         "risk_tier": "medium", "options": options}}


def validate_proposal(state: AgentState) -> dict:
    """Independently re-checks every option from its final changes alone, using the shared constraint code."""
    proposal = state.get("proposal") or {}
    if proposal.get("needs_clarification"):
        return {"proposal": {**proposal, "validated": True}}

    items = C.build_items(state["activities"])
    affected_ids = {a["_id"] for a in (state.get("relevant_activities") or [])}
    affected = [items[i] for i in affected_ids if i in items]
    intent = P.intent_from_understood(state.get("understood") or {}, items)
    blocked = P.build_blocked(intent, affected)
    ml = state.get("ml_predictions") or {}

    checked = []
    for opt in proposal.get("options", []):
        scoped = [c for c in opt["changes"] if c["activity_id"] in affected_ids]
        removed = len(opt["changes"]) - len(scoped)
        ev = C.evaluate_option(scoped, items, blocked)
        warnings, notes = C.describe_evaluation(ev, items)
        ml_notes = C.ml_overrun_notes(ev, items, ml)
        notes.extend(ml_notes)
        if removed:
            notes.append(f"{removed} change(s) outside the affected activities were discarded")
        conflict_ids = sorted({i for pair in ev["new_pairs"] for i in pair})
        checked.append({
            **opt,
            "changes": scoped,
            "risk": C.risk_level(warnings, ev, bool(ml_notes)),
            "checks": {"warnings": warnings, "notes": notes, "conflict_activity_ids": conflict_ids},
        })

    checked.sort(key=lambda o: len(o["checks"]["warnings"]) > 0)  # stable: clean options first
    risk = checked[0]["risk"] if checked else "low"
    _emit(state, "validate_proposal", "done", "Checked against the real schedule")
    return {"proposal": {**proposal, "options": checked, "risk_tier": risk, "validated": True}, "validated": True}


def _pretty_change(c: dict, items: dict) -> str:
    it = items.get(c["activity_id"])
    title = it.title if it else "Activity"
    if c["field"] == "scheduledStart":
        try:
            return f"{title}: start -> {fmt_local(parse_datetime(c['new_value']))}"
        except ValueError:
            return f"{title}: start changes"
    return f"{title}: {c['field']} -> {c['new_value']}"


def annotate_rules(state: AgentState) -> dict:
    """Advisory only: attaches notes from retrieved documents. Never alters a change, fails soft."""
    proposal = state.get("proposal") or {}
    documents = state.get("documents") or []
    if proposal.get("needs_clarification") or not documents or not proposal.get("options"):
        return {}

    items = C.build_items(state["activities"])
    option_lines = "\n".join(
        f"Option {i}: {opt['description']} ({'; '.join(_pretty_change(c, items) for c in opt['changes'][:6])})"
        for i, opt in enumerate(proposal["options"], start=1)
    )
    prompt = (
        "Excerpts from this schedule's organizer documents:\n"
        + "\n---\n".join(documents[:3])[:1800]
        + "\n\nProposed options (already verified conflict-free by software):\n" + option_lines
        + "\n\nFor each option, add a short note ONLY if an excerpt directly applies - a rule the option may "
        "break, or a requirement it satisfies. Quote the rule briefly. Do not invent rules. "
        "Return an empty list if no excerpt is relevant."
    )
    try:
        result = fast_llm.with_structured_output(RuleNotes, method="json_schema", strict=False).invoke(prompt)
    except Exception as error:
        print(f"[annotate_rules] skipped: {error}")
        return {}

    sources = state.get("sources") or []
    label = ", ".join(sources) if sources else "uploaded documents"
    options = [dict(o) for o in proposal["options"]]
    for n in result.notes:
        if 1 <= n.option_index <= len(options):
            checks = dict(options[n.option_index - 1].get("checks") or {})
            checks["notes"] = list(checks.get("notes", [])) + [f"Rule check ({label}): {n.note}"]
            options[n.option_index - 1]["checks"] = checks
    return {"proposal": {**proposal, "options": options, "sources": sources}}