"""Graph state shared by every node."""

from dataclasses import dataclass, field
from typing import Annotated, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

Route = Literal["order_management", "building_plan"]


@dataclass
class HomeFixState:
    """Conversation messages plus the specialist the router picked for this turn.

    ``messages`` uses the ``add_messages`` reducer: a node returns only the messages it adds, and
    they are appended to (or, by matching id, replace) the existing history.
    """

    messages: Annotated[list[AnyMessage], add_messages] = field(default_factory=list)
    route: Route | None = None
