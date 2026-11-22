"""Router node: classifies each request and picks the specialist that handles it.

Model: ``homefix-llama-3-1-8b-instruct`` (small and fast; classification only, no tools).
"""

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.constants import TAG_NOSTREAM

from agent.common import ROUTER_MODEL, chat_model, conversation

ORDER_MANAGEMENT = "order_management"
BUILDING_PLAN = "building_plan"
Route = Literal["order_management", "building_plan"]

SYSTEM_PROMPT = """You route requests for HomeFix, a home-improvement store.

Reply with exactly one word:
- ORDER: the user asks about orders - status, tracking, delivery, listing, cancelling, returns.
- PLAN: the user wants help planning or building a home project - materials, quantities, cost
  estimates, permits, how-to steps, design ideas. Use PLAN for anything else too.

Classify the LAST user message, using earlier messages only as context."""

# Only the most recent turns matter for routing; keeps the 8B model's prompt short.
_CONTEXT_MESSAGES = 6


def _parse(label: str) -> Route:
    return ORDER_MANAGEMENT if "ORDER" in label.upper() else BUILDING_PLAN


async def router(state: dict) -> dict:
    """Classify the latest request and store the route in state."""
    recent = conversation(state["messages"])[-_CONTEXT_MESSAGES:]
    # nostream: the routing label is internal, so keep its tokens out of the client's delta stream.
    model = chat_model(ROUTER_MODEL, temperature=0, max_tokens=5).with_config(
        tags=[TAG_NOSTREAM]
    )
    transcript = "\n".join(
        f"{'User' if isinstance(m, HumanMessage) else 'Assistant'}: {m.text}" for m in recent
    )
    reply = await model.ainvoke(
        [SystemMessage(SYSTEM_PROMPT), HumanMessage(f"Conversation:\n{transcript}\n\nLabel:")]
    )
    return {"route": _parse(reply.text)}


def next_node(state: dict) -> Route:
    """Conditional edge: the specialist the router picked."""
    return state.get("route") or BUILDING_PLAN
