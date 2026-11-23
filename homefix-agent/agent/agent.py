"""HomeFix agent graph.

Every request goes to the router (Llama 3.1 8B), which sends it to one specialist:

    START -> router -> order_management (Llama 3.3 70B) -> END
                    -> building_plan   (GPT 6 sol)      -> END

Each node lives in ``agent/node/`` with its own tools; models are the AI Gateway model services
created by ``databricks-scripts/scripts/deploy.sh`` (names in ``agent/common.py``).
"""

import os
from collections.abc import AsyncGenerator, Callable
from typing import Any

from databricks.sdk import WorkspaceClient
from langchain.agents.middleware.human_in_the_loop import HITLResponse
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command
from pydantic import TypeAdapter, ValidationError

from agent.common import ROUTER_MODEL, app_workspace_client
from agent.node import REQUIRE_APPROVAL, building_plan, order_management
from agent.node.router import BUILDING_PLAN, ORDER_MANAGEMENT, next_node, router
from databricks_agentkit.langgraph import (
    checkpointer,
    configure_tracing,
    genie_tools,
    memory_tools,
    start_trace,
    thread_config,
)
from databricks_agentkit.langgraph.session_store import (
    invocation_id_from_metadata,
    invocation_metadata,
)
from databricks_agentkit.runtime.auth import AuthError

# Nodes that run a specialist agent as a subgraph (see run_agent's streaming).
SPECIALIST_NODES = frozenset({ORDER_MANAGEMENT, BUILDING_PLAN})

# The chat app shows this as the agent's default model. The graph doesn't use a single model: each
# node uses its own model service, so the chat app's model picker has no effect.
MODEL = ROUTER_MODEL

__all__ = ["MODEL", "REQUIRE_APPROVAL", "configure", "create_agent_graph", "run_agent"]


class HomeFixState(MessagesState):
    """Conversation messages plus the specialist the router picked for this turn."""

    route: str


def configure() -> None:
    """Wire up global state; call once at server startup (not at import)."""
    _check_databricks_auth()
    configure_tracing()


def _check_databricks_auth() -> None:
    """Fail fast at startup with a clear message if Databricks auth isn't configured."""
    try:
        app_workspace_client()
    except Exception as e:
        profile = os.getenv("DATABRICKS_CONFIG_PROFILE")
        target = (
            f"profile {profile!r}" if profile else "the DEFAULT profile / DATABRICKS_HOST+TOKEN"
        )
        raise RuntimeError(
            f"Databricks auth is not configured — the agent can't call the model. Tried {target}.\n"
            "Fix one of:\n"
            "  • set DATABRICKS_CONFIG_PROFILE in .env to a profile from `databricks auth profiles`, or\n"
            "  • run `databricks auth login --profile <name>` to create one, or\n"
            "  • set DATABRICKS_HOST and DATABRICKS_TOKEN in .env.\n"
            f"(underlying error: {e})"
        ) from e


async def create_agent_graph(
    actor: str,
    model: str | None = None,
    *,
    workspace_client_for: Callable[[str], WorkspaceClient] | None = None,
):
    """Build the router -> specialist graph.

    ``actor`` scopes the long-term-memory tools. ``model`` is accepted for runtime compatibility
    and ignored: each node uses its own model service. Memory and Genie tools from ``agent.toml``
    bindings (empty when unbound) are given to both specialists.
    """
    shared_tools = [
        *memory_tools(actor),
        *genie_tools(workspace_client_for=workspace_client_for),
    ]

    builder = StateGraph(HomeFixState)
    builder.add_node("router", router)
    builder.add_node(ORDER_MANAGEMENT, order_management.build(shared_tools))
    builder.add_node(BUILDING_PLAN, building_plan.build(shared_tools))

    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", next_node, [ORDER_MANAGEMENT, BUILDING_PLAN])
    builder.add_edge(ORDER_MANAGEMENT, END)
    builder.add_edge(BUILDING_PLAN, END)
    return builder.compile(checkpointer=checkpointer())


async def recovery_input(
    agent_input: Any,
    *,
    session_id: str,
    actor: str,
    invocation_id: str,
) -> Any:
    """Return the input that should be passed to ``run_agent`` after worker loss.

    A checkpoint tagged with this invocation means LangGraph can continue by receiving ``None``.
    Otherwise recovery replays the original application input.
    """
    checkpoint = await checkpointer().aget_tuple(thread_config(session_id, actor))
    current_invocation_checkpointed = bool(
        checkpoint and invocation_id_from_metadata(checkpoint.metadata) == invocation_id
    )
    return None if current_invocation_checkpointed else agent_input


async def _validate_approval_resume(graph: Any, config: dict, agent_input: Any) -> None:
    """Reject malformed middleware decisions before LangGraph checkpoints the resume value."""
    if not isinstance(agent_input, Command) or agent_input.resume is None:
        return
    state = await graph.aget_state(config)
    pending = [item for task in state.tasks for item in task.interrupts]
    resume = agent_input.resume
    keyed = isinstance(resume, dict) and any(item.id in resume for item in pending)
    for item in pending:
        if not isinstance(item.value, dict) or "review_configs" not in item.value:
            continue  # Application-defined interrupts keep their native resume contract.
        if keyed and item.id not in resume:
            continue
        response = resume[item.id] if keyed else resume
        try:
            decisions = TypeAdapter(HITLResponse).validate_python(response)["decisions"]
        except ValidationError as error:
            raise AuthError(
                "INVALID_APPROVAL", "Invalid approval decision payload.", 400
            ) from error
        reviews = item.value["review_configs"]
        if len(decisions) != len(reviews) or any(
            decision["type"] not in review["allowed_decisions"]
            for decision, review in zip(decisions, reviews, strict=True)
        ):
            raise AuthError(
                "INVALID_APPROVAL", "Provide one allowed decision per pending tool call.", 400
            )


async def run_agent(
    agent_input: Any,
    *,
    session_id: str,
    actor: str | None = None,
    model: str | None = None,
    invocation_id: str | None = None,
    workspace_client_for: Callable[[str], WorkspaceClient] | None = None,
    graph: Any = None,
) -> AsyncGenerator[Any, None]:
    """Run the agent and yield native LangGraph stream events.

    This is the framework-native entrypoint. It has no dependency on Agent Bricks request or context types,
    so it can be called from another server, a notebook, or a test harness.
    """
    # Each interactive resume builds tools from this request's credentials. Only graph state,
    # never the graph or its credential-bound clients, survives the approval pause.
    actor = actor or session_id
    if graph is None:
        graph = await create_agent_graph(actor, model, workspace_client_for=workspace_client_for)
    config = thread_config(session_id, actor)
    if invocation_id:
        config["metadata"] = invocation_metadata(invocation_id)

    await _validate_approval_resume(graph, config, agent_input)

    last_update: Any = None
    with start_trace(name="invoke", inputs=agent_input, session_id=session_id) as span:
        # subgraphs=True streams from inside each specialist (its own agent graph) as it runs:
        # model tokens, tool calls, and tool results arrive as they happen instead of all at once
        # when the specialist node finishes. Events keep the (mode, payload) shape.
        async for namespace, mode, payload in graph.astream(
            input=agent_input,
            config=config,
            stream_mode=["updates", "messages"],
            durability="sync",
            subgraphs=True,
        ):
            if mode == "updates":
                if not namespace:
                    last_update = payload
                    # A specialist node's update repeats the messages already streamed from inside
                    # its subgraph; keep only the router and the interrupt.
                    payload = {
                        k: v for k, v in payload.items() if k not in SPECIALIST_NODES
                    }
                elif "__interrupt__" in payload:
                    continue  # the parent graph reports the same interrupt
                if not payload:
                    continue
            yield mode, payload
        if span is not None and last_update is not None:
            span.set_outputs(last_update)
