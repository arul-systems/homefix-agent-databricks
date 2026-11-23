"""Order management node: order status, history, and cancellation.

Model: ``homefix-llama-3-3-70b-instruct``. The tools below use in-memory sample orders; swap their
bodies for calls to the real order system.
"""

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from agent.common import ORDER_MANAGEMENT_MODEL, chat_model, run_specialist
from agent.state import HomeFixState

SYSTEM_PROMPT = """You are the HomeFix order management assistant.
Help customers check order status, review their orders, and cancel orders.
Always use the tools to look up order data; never invent order numbers, dates, or totals.
If the customer doesn't give an order ID, list their orders first and ask which one they mean.
Only orders that haven't shipped can be cancelled."""

_ORDERS: dict[str, dict[str, Any]] = {
    "HF-1001": {
        "items": ["Cordless drill", "Drill bit set"],
        "total": 189.98,
        "status": "delivered",
        "eta": "2026-11-12",
    },
    "HF-1002": {
        "items": ["Composite decking boards x40", "Deck screws (5 lb)"],
        "total": 1499.50,
        "status": "shipped",
        "eta": "2026-11-25",
    },
    "HF-1003": {
        "items": ["Interior paint, eggshell, 5 gal", "Roller kit"],
        "total": 214.37,
        "status": "processing",
        "eta": "2026-11-28",
    },
}


@tool
def list_orders() -> list[dict[str, Any]]:
    """List the customer's orders with their status, total, and estimated delivery date."""
    return [
        {"order_id": oid, "status": o["status"], "total": o["total"], "eta": o["eta"]}
        for oid, o in _ORDERS.items()
    ]


@tool
def get_order_status(order_id: str) -> dict[str, Any]:
    """Get the full details of one order: items, total, status, and estimated delivery date."""
    order = _ORDERS.get(order_id.strip().upper())
    if order is None:
        return {"error": f"No order found with ID {order_id}."}
    return {"order_id": order_id.upper(), **order}


@tool
def cancel_order(order_id: str, reason: str) -> str:
    """Cancel an order that hasn't shipped yet. Requires the order ID and the customer's reason."""
    oid = order_id.strip().upper()
    order = _ORDERS.get(oid)
    if order is None:
        return f"No order found with ID {order_id}."
    if order["status"] != "processing":
        return f"Order {oid} is already {order['status']} and can't be cancelled."
    order["status"] = "cancelled"
    return f"Order {oid} cancelled. Reason: {reason}. A refund of ${order['total']:.2f} is on its way."


TOOLS: list[BaseTool] = [list_orders, get_order_status, cancel_order]

# Tools that pause for human approval before running (see HumanInTheLoopMiddleware).
REQUIRE_APPROVAL = {"cancel_order": True}


def build(shared_tools: list[BaseTool]):
    """Build this node's graph node, given tools shared by every specialist (memory, Genie)."""
    agent = create_agent(
        model=chat_model(ORDER_MANAGEMENT_MODEL),
        tools=[*TOOLS, *shared_tools],
        system_prompt=SYSTEM_PROMPT,
        middleware=[HumanInTheLoopMiddleware(interrupt_on=REQUIRE_APPROVAL)],
        name="order_management_agent",
    )

    async def order_management(state: HomeFixState, config: RunnableConfig) -> dict:
        return await run_specialist(agent, state, config)

    return order_management
