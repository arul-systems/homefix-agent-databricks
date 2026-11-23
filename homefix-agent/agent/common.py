"""Shared helpers for the graph nodes: AI Gateway model services and specialist execution.

Every node calls one of the AI Gateway model services created by
``databricks-scripts/scripts/deploy.sh``. Models are queried through the gateway with the app's own
Databricks credentials (no per-endpoint access token).
"""

import os
from functools import lru_cache
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks_agentkit import workspace_client, workspace_headers
from databricks_agentkit.langgraph.messages import gateway_messages
from databricks_langchain import ChatDatabricks
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from agent.state import HomeFixState

# Unity Catalog schema that holds the model services (bundle variables `catalog` and `schema`).
MODEL_SERVICE_SCHEMA = os.getenv(
    "HOMEFIX_MODEL_SERVICE_SCHEMA", "agent_workspace_7474653685104191.homefix_ai"
)

# Model service per node. Each can be overridden on its own, e.g. to point at another schema.
ROUTER_MODEL = os.getenv(
    "HOMEFIX_ROUTER_MODEL", f"{MODEL_SERVICE_SCHEMA}.homefix-llama-3-1-8b-instruct"
)
ORDER_MANAGEMENT_MODEL = os.getenv(
    "HOMEFIX_ORDER_MANAGEMENT_MODEL", f"{MODEL_SERVICE_SCHEMA}.homefix-llama-3-3-70b-instruct"
)
BUILDING_PLAN_MODEL = os.getenv(
    "HOMEFIX_BUILDING_PLAN_MODEL", f"{MODEL_SERVICE_SCHEMA}.homefix-gpt-6-sol"
)


class _RoutedChatDatabricks(ChatDatabricks):
    """Forward account-host workspace routing to the underlying OpenAI clients."""

    def _get_client_kwargs(self) -> dict[str, Any]:
        kwargs = super()._get_client_kwargs()
        if headers := workspace_headers():
            kwargs["default_headers"] = headers
        return kwargs

    def _prepare_inputs(self, messages: list[Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        return super()._prepare_inputs(gateway_messages(messages), *args, **kwargs)


@lru_cache(maxsize=1)
def app_workspace_client() -> WorkspaceClient:
    """The App's own client, shared across turns.

    Constructing a WorkspaceClient resolves auth with blocking network calls, so build it once; the
    SDK refreshes tokens itself.
    """
    return workspace_client()


def chat_model(model_service: str, **kwargs: Any) -> ChatDatabricks:
    """A chat model for an AI Gateway model service (``catalog.schema.name``)."""
    # use_ai_gateway routes to `<host>/ai-gateway/mlflow/v1`, where the model is the UC model
    # service name rather than a serving-endpoint name.
    return _RoutedChatDatabricks(
        endpoint=model_service,
        workspace_client=app_workspace_client(),
        use_ai_gateway=True,
        **kwargs,
    )


def conversation(messages: list[AnyMessage]) -> list[AnyMessage]:
    """The user-visible conversation: user turns and final assistant answers.

    Tool calls and tool results stay in graph state for the UI and history, but are dropped from
    what the next node sees: each specialist has its own tools, and another node's tool calls would
    reference tools it doesn't have.
    """
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        or (isinstance(m, AIMessage) and not m.tool_calls and m.text.strip())
    ]


async def run_specialist(agent: Any, state: HomeFixState, config: RunnableConfig) -> dict:
    """Run a specialist agent on the conversation and return only the messages it added.

    The agent runs as a subgraph inside the node, so it shares the parent's checkpointer: a
    human-approval interrupt pauses the whole graph and resuming continues inside the subgraph.
    """
    history = conversation(state.messages)
    seen = {m.id for m in history}
    result = await agent.ainvoke({"messages": history}, config)
    return {"messages": [m for m in result["messages"] if m.id not in seen]}
