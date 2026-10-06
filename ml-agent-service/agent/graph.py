from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent import nodes


def route_after_intent(state: AgentState) -> str:
    if state["intent"] == "general_chat":
        return "answer_general_chat"
    if state["intent"] == "schedule_query":
        return "answer_schedule_query"
    if state["intent"] == "knowledge_query":
        return "retrieve_for_knowledge"
    return "understand_request"


def _route_to_ml_or_plan(state: AgentState) -> str:
    return "get_ml_prediction" if state.get("relevant_activities") else "plan_changes"


def route_after_understand(state: AgentState) -> str:
    if state["understood"].get("needs_knowledge"):
        return "retrieve"
    return _route_to_ml_or_plan(state)


def route_after_grade(state: AgentState) -> str:
    if state.get("grade_passed") or state["generation_count"] >= 2:
        return _route_to_ml_or_plan(state)
    return "rewrite"


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("route_intent", nodes.route_intent)
    builder.add_node("answer_general_chat", nodes.answer_general_chat)
    builder.add_node("answer_schedule_query", nodes.answer_schedule_query)
    builder.add_node("retrieve_for_knowledge", nodes.retrieve)
    builder.add_node("answer_knowledge_query", nodes.answer_knowledge_query)

    builder.add_node("understand_request", nodes.understand_request)
    builder.add_node("retrieve", nodes.retrieve)
    builder.add_node("grade", nodes.grade)
    builder.add_node("rewrite", nodes.rewrite)
    builder.add_node("get_ml_prediction", nodes.get_ml_prediction)
    builder.add_node("plan_changes", nodes.plan_changes)
    builder.add_node("validate_proposal", nodes.validate_proposal)
    builder.add_node("annotate_rules", nodes.annotate_rules)

    builder.add_edge(START, "route_intent")
    builder.add_conditional_edges("route_intent", route_after_intent, {
        "answer_general_chat": "answer_general_chat",
        "answer_schedule_query": "answer_schedule_query",
        "retrieve_for_knowledge": "retrieve_for_knowledge",
        "understand_request": "understand_request",
    })
    builder.add_edge("answer_general_chat", END)
    builder.add_edge("answer_schedule_query", END)
    builder.add_edge("retrieve_for_knowledge", "answer_knowledge_query")
    builder.add_edge("answer_knowledge_query", END)

    # Everything below this line is completely unchanged from before this turn.
    builder.add_conditional_edges("understand_request", route_after_understand, {
        "retrieve": "retrieve",
        "get_ml_prediction": "get_ml_prediction",
        "plan_changes": "plan_changes",
    })
    builder.add_edge("retrieve", "grade")
    builder.add_conditional_edges("grade", route_after_grade, {
        "get_ml_prediction": "get_ml_prediction",
        "plan_changes": "plan_changes",
        "rewrite": "rewrite",
    })
    builder.add_edge("rewrite", "retrieve")
    builder.add_edge("get_ml_prediction", "plan_changes")
    builder.add_edge("plan_changes", "validate_proposal")
    builder.add_edge("validate_proposal", "annotate_rules")
    builder.add_edge("annotate_rules", END)
    return builder.compile()


_graph = build_graph()


def run_agent(schedule_id: str, schedule_name: str, activities: list, raw_message: str, reference_entries: list = None, emit_progress=None) -> dict:
    result = _graph.invoke({
        "schedule_id": schedule_id,
        "schedule_name": schedule_name,
        "activities": activities,
        "reference_entries": reference_entries or [],
        "raw_message": raw_message,
        "understood": {},
        "relevant_activities": [],
        "rewritten_query": None,
        "documents": [],
        "sources": [],
        "generation_count": 0,
        "grade_passed": False,
        "ml_predictions": {},
        "proposal": None,
        "validated": False,
        "intent": "",
        "emit_progress": emit_progress,
    })
    return {"understood": result["understood"], "proposal": result["proposal"]}