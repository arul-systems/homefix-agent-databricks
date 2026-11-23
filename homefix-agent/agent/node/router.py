"""Router node: classifies each request and picks the specialist that handles it.

Model: ``homefix-llama-3-1-8b-instruct`` (small and fast; classification only, no tools).
"""

from langchain_core.messages import SystemMessage
from langgraph.constants import TAG_NOSTREAM
from pydantic import BaseModel, Field

from agent.common import ROUTER_MODEL, chat_model, conversation
from agent.state import HomeFixState, Route

ORDER_MANAGEMENT = "order_management"
BUILDING_PLAN = "building_plan"

SYSTEM_PROMPT = """You route requests for HomeFix, a home-improvement store.

Pick the route:
- order_management: the user asks about orders - status, tracking, delivery, listing, cancelling,
  returns.
- building_plan: the user wants help planning or building a home project - materials, quantities,
  cost estimates, permits, how-to steps, design ideas. Use building_plan for anything else too.

Classify the LAST user message, using earlier messages only as context."""


class Rationale(BaseModel):
    """Why the router picked this route."""

    reason: str = Field(description="One short sentence explaining the choice.")
    confidence: float = Field(
        ge=0, le=1, description="Confidence in the route, from 0 to 1."
    )


class RouteDecision(BaseModel):
    """The specialist that should handle the latest user message."""

    route: Route = Field(description="The specialist to send the request to.")
    rationale: Rationale


async def router(state: HomeFixState) -> dict:
    """Classify the latest request and store the route in state."""
    history = conversation(state.messages)
    # nostream: the routing label is internal, so keep its tokens out of the client's delta stream.
    model = (
        chat_model(ROUTER_MODEL, temperature=0, max_tokens=200)
        .with_structured_output(RouteDecision, method="json_schema")
        .with_config(tags=[TAG_NOSTREAM])
    )
    decision: RouteDecision = await model.ainvoke(
        [SystemMessage(SYSTEM_PROMPT), *history]
    )
    return {"route": decision.route}


def next_node(state: HomeFixState) -> Route:
    """Conditional edge: the specialist the router picked."""
    return state.route or BUILDING_PLAN
