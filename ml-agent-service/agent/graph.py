from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent import nodes


def _route_to_ml_or_propose(state: AgentState) -> str:
    return "get_ml_prediction" if state.get("relevant_activity") else "generate_proposal"


def route_after_understand(state: AgentState) -> str:
    if state["understood"].get("needs_knowledge"):
        return "retrieve"
    return _route_to_ml_or_propose(state)


def route_after_grade(state: AgentState) -> str:
    if state.get("grade_passed") or state["generation_count"] >= 2:
        return _route_to_ml_or_propose(state)
    return "rewrite"


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("understand_request", nodes.understand_request)
    builder.add_node("retrieve", nodes.retrieve)
    builder.add_node("grade", nodes.grade)
    builder.add_node("rewrite", nodes.rewrite)
    builder.add_node("get_ml_prediction", nodes.get_ml_prediction)
    builder.add_node("generate_proposal", nodes.generate_proposal)
    builder.add_node("validate_proposal", nodes.validate_proposal)

    builder.add_edge(START, "understand_request")

    builder.add_conditional_edges("understand_request", route_after_understand, {
        "retrieve": "retrieve",
        "get_ml_prediction": "get_ml_prediction",
        "generate_proposal": "generate_proposal",
    })

    builder.add_edge("retrieve", "grade")

    builder.add_conditional_edges("grade", route_after_grade, {
        "get_ml_prediction": "get_ml_prediction",
        "generate_proposal": "generate_proposal",
        "rewrite": "rewrite",
    })

    builder.add_edge("rewrite", "retrieve")
    builder.add_edge("get_ml_prediction", "generate_proposal")
    builder.add_edge("generate_proposal", "validate_proposal")
    builder.add_edge("validate_proposal", END)

    return builder.compile()


_graph = build_graph()


def run_agent(schedule_id: str, schedule_name: str, activities: list, raw_message: str, emit_progress=None) -> dict:
    result = _graph.invoke({
        "schedule_id": schedule_id,
        "schedule_name": schedule_name,
        "activities": activities,
        "raw_message": raw_message,
        "understood": None,
        "relevant_activity": None,
        "rewritten_query": None,
        "documents": [],
        "sources": [],
        "generation_count": 0,
        "grade_passed": False,
        "ml_prediction": None,
        "proposal": None,
        "validated": False,
        "emit_progress": emit_progress,
    })
    return {"understood": result["understood"], "proposal": result["proposal"]}