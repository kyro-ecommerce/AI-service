from typing import Annotated, Any, Sequence, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """LangGraph ReAct Agent State — shared memory for the single Tool-Calling Agent."""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    user_id: int
    user_message: str
    recommended_products: list[dict[str, Any]]
    final_reply: str
