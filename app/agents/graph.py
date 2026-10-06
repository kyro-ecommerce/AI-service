import logging
from sqlalchemy.orm import Session

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from app.agents.nodes import build_react_agent_node
from app.agents.state import AgentState

logger = logging.getLogger("ai-service.agents.graph")


def build_multi_agent_graph(db: Session | None = None, with_checkpointer: bool = False):
    """Construct and compile the LangGraph ReAct Tool-Calling Agent graph.

    Args:
        db: Optional database session
        with_checkpointer: If True, attach MemorySaver checkpointer (requires thread_id in config when invoking)

    Architecture (single ReAct agent):

        [START]
           │
           ▼
    ┌─────────────────┐
    │  react_agent    │  ← Gemini + Fallback + 5 Tools (search, compare, stock, voucher, order)
    └────────┬────────┘
             │
             ▼
           [END]
    """
    workflow = StateGraph(AgentState)

    # Single node: ReAct agent with all tools
    react_node = build_react_agent_node(db=db)
    workflow.add_node("react_agent", react_node)

    # Simple linear flow: START → agent → END
    workflow.set_entry_point("react_agent")
    workflow.add_edge("react_agent", END)

    if with_checkpointer:
        checkpointer = MemorySaver()
        app_graph = workflow.compile(checkpointer=checkpointer)
    else:
        app_graph = workflow.compile()

    logger.info("LangGraph ReAct Agent graph compiled successfully (checkpointer=%s).", with_checkpointer)
    return app_graph
