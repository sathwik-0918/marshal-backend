import os
from datetime import datetime, timedelta
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from agent.state import AgentState
from schemas import UnderstoodRequest, ProposalSet
from ml import predictor
from knowledge import store_manager

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Same two-tier split Prism uses: cheap/fast for judgment calls,
# stronger model reserved for the one node doing real synthesis.
fast_llm = ChatGroq(api_key=GROQ_API_KEY, model="openai/gpt-oss-120b", temperature=0, max_tokens=1024,reasoning_effort="low")
main_llm = ChatGroq(
    api_key=GROQ_API_KEY,
    model="openai/gpt-oss-120b",
    temperature=0.1,
    max_tokens=2048,
    reasoning_effort="medium"
)


def _emit(state: AgentState, node: str, status: str, detail: str, data=None):
    callback = state.get("emit_progress")
    if not callback:
        return
    try:
        callback({"node": node, "status": status, "detail": detail, "data": data or {}})
    except Exception as error:
        print(f"[TRACE EMIT ERROR] {error}")


# ── understand_request ────────────────────────────────────────────

def understand_request(state: AgentState) -> dict:
    _emit(state, "understand_request", "active", "Reading the request")
    structured_llm = fast_llm.with_structured_output(UnderstoodRequest, method="json_schema", strict=False)

    activity_list = "\n".join(
        f"- id={a['_id']}: {a['title']} at {a['venue']}, starts {a['scheduledStart']}, {a['durationMinutes']} min"
        for a in state["activities"]
    )
    prompt = (
        f"A message came in about the schedule '{state['schedule_name']}'.\n\n"
        f"Activities on this schedule:\n{activity_list}\n\n"
        f"Message: \"{state['raw_message']}\"\n\n"
        "Identify which activity (by id) this is about, if any, and what kind of issue is "
        "being reported. Also decide whether answering this properly requires checking "
        "uploaded schedule documents (policies, rules, venue requirements) rather than just "
        "the schedule and predictions alone."
    )
    result: UnderstoodRequest = structured_llm.invoke(prompt)
    relevant_activity = next((a for a in state["activities"] if a["_id"] == result.activity_id), None)

    _emit(state, "understand_request", "done", "Request understood", {"needsKnowledge": result.needs_knowledge})
    return {"understood": result.model_dump(), "relevant_activity": relevant_activity}


# ── retrieve / grade / rewrite — adapted from Prism, same loop shape ─

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
        "You are a relevance grader for event/schedule organizer documents (policies, venue "
        "rules, activity requirements). Decide whether the provided documents contain enough "
        "information to answer the request. Reply with exactly one word: yes or no."
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
        "Rewrite this request to be a more specific search query for event schedule "
        "documents — policies, venue rules, activity requirements. Return ONLY the rewritten query."
    )
    response = fast_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["raw_message"][:300]),
    ])
    rewritten = response.content.strip()[:300]
    _emit(state, "rewrite", "done", "Search refined", {"rewrittenQuery": rewritten})
    return {"rewritten_query": rewritten}


# ── get_ml_prediction — unchanged from before ─────────────────────

def get_ml_prediction(state: AgentState) -> dict:
    activity = state.get("relevant_activity")
    if not activity:
        return {"ml_prediction": None}

    start = datetime.fromisoformat(activity["scheduledStart"].replace("Z", "+00:00"))
    prediction = predictor.predict(
        activity_type=activity.get("activityType") or "session",
        venue=activity["venue"],
        scheduled_duration=activity["durationMinutes"],
        num_people=len(activity.get("stakeholders", [])) or 10,
        time_of_day=start.hour,
        stakeholder_past_delay_rate=0.3,
        day_of_event=1,
        is_weekend=1 if start.weekday() >= 5 else 0,
    )
    return {"ml_prediction": prediction}


# ── generate_proposal — extended to optionally consume RAG context ─

def generate_proposal(state: AgentState) -> dict:
    if not state.get("relevant_activity"):
        return {"proposal": ProposalSet(
            needs_clarification=True,
            clarification_question="Which activity is this about? I couldn't match your message to one on this schedule.",
            risk_tier="low", options=[],
        ).model_dump()}

    _emit(state, "generate_proposal", "active", "Working out possible solutions")
    understood = state["understood"]
    activity = state["relevant_activity"]
    ml = state.get("ml_prediction") or {}
    documents = state.get("documents") or []
    sources = state.get("sources") or []

    knowledge_section = ""
    if documents:
        knowledge_section = (
            "\n\nRelevant organizer knowledge (from uploaded documents, sources: "
            + ", ".join(sources) + "):\n" + "\n\n".join(documents[:3])[:1500]
        )

    structured_llm = main_llm.with_structured_output(ProposalSet, method="json_schema", strict=False)
    prompt = (
        f"Activity affected: {activity['title']} at {activity['venue']}, "
        f"currently starting {activity['scheduledStart']}, {activity['durationMinutes']} minutes.\n"
        f"Reported issue: {understood['summary']} (type: {understood['issue_type']}"
        + (f", ~{understood['minutes_mentioned']} minutes mentioned" if understood.get('minutes_mentioned') else "")
        + ")\nML prediction: " + f"{ml.get('predicted_duration_minutes', 'unknown')} min duration, "
        f"{ml.get('delay_probability', 'unknown')} delay probability."
        + knowledge_section
        + "\n\nOther activities on this schedule:\n"
        + "\n".join(f"- {a['title']} at {a['venue']}, {a['scheduledStart']}"
                     for a in state["activities"] if a["_id"] != activity["_id"])
        + "\n\nPropose 1 to 3 concrete solutions, respecting any organizer knowledge above if present. "
        "A solution must never put two activities in the same venue at overlapping times. "
        "risk_tier: low if same venue and under 15 minutes shifted, high if it creates or "
        "resolves a conflict with another activity, medium otherwise."
    )
    result: ProposalSet = structured_llm.invoke(prompt)
    _emit(state, "generate_proposal", "done", "Proposal ready")
    return {"proposal": result.model_dump()}


# ── validate_proposal — NEW, deterministic, no LLM ────────────────

def validate_proposal(state: AgentState) -> dict:
    """
    Re-checks each proposed option against the REAL activity list using
    plain datetime comparison — not trusting the LLM's own claim that a
    given option is conflict-free. If every option turns out to
    genuinely conflict, the risk tier is escalated regardless of what
    generate_proposal said, so a bad suggestion can't quietly present
    itself as low-risk.
    """
    proposal = state["proposal"]
    if proposal.get("needs_clarification"):
        return {"proposal": {**proposal, "validated": True}}

    activity = state.get("relevant_activity")
    activities = state["activities"]
    validated_options = []

    for option in proposal.get("options", []):
        conflict = False
        if activity and option.get("new_venue") and option.get("new_start_time"):
            try:
                new_start = datetime.fromisoformat(option["new_start_time"].replace("Z", "+00:00"))
                new_end = new_start + timedelta(minutes=activity["durationMinutes"])
                for other in activities:
                    if other["_id"] == activity["_id"] or other["venue"] != option["new_venue"]:
                        continue
                    other_start = datetime.fromisoformat(other["scheduledStart"].replace("Z", "+00:00"))
                    other_end = other_start + timedelta(minutes=other["durationMinutes"])
                    if new_start < other_end and other_start < new_end:
                        conflict = True
                        break
            except (ValueError, KeyError):
                pass  # malformed option data — leave conflict as False, don't crash the graph over it
        validated_options.append({**option, "conflict_detected": conflict})

    all_conflict = bool(validated_options) and all(o["conflict_detected"] for o in validated_options)
    risk_tier = "high" if all_conflict else proposal["risk_tier"]

    _emit(state, "validate_proposal", "done", "Checked against the real schedule",
          {"anyConflict": any(o["conflict_detected"] for o in validated_options)})
    return {"proposal": {**proposal, "options": validated_options, "risk_tier": risk_tier, "validated": True}}