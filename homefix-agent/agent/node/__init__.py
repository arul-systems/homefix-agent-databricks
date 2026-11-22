"""Graph nodes. Each module is one node; its tools live in the same file.

- ``router``: picks a specialist (Llama 3.1 8B)
- ``order_management``: orders (Llama 3.3 70B)
- ``building_plan``: project planning (GPT 6 sol)
"""

from langchain_core.tools import BaseTool

from agent.node import building_plan, order_management


def all_tools() -> list[BaseTool]:
    """Every local tool across the specialist nodes (shown in the chat app's Tools pane)."""
    return [*order_management.TOOLS, *building_plan.TOOLS]


# Tools that require human approval, across all nodes.
REQUIRE_APPROVAL = {**order_management.REQUIRE_APPROVAL, **building_plan.REQUIRE_APPROVAL}
