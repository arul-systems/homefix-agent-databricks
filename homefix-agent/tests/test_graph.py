"""Hermetic tests for the router -> specialist graph, using scripted fake models (no Databricks)."""

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.types import Command

import agent.agent as agent_module
from agent.node import building_plan, order_management, router


class _ScriptedModel(GenericFakeChatModel):
    """Fake chat model that returns scripted messages and accepts tool binding."""

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self


def _model(*messages: AIMessage) -> _ScriptedModel:
    # The fake can't stream tool-call messages with empty content, so it never streams.
    return _ScriptedModel(messages=iter(messages), disable_streaming=True)


@pytest.fixture
def graph_factory(monkeypatch):
    import databricks_agentkit.langgraph.session_store as ss

    monkeypatch.setattr(ss, "_saver", None)  # fresh in-memory checkpointer per test
    monkeypatch.delenv("AGENT_SESSION_STORE", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_STORE", raising=False)
    monkeypatch.setattr(agent_module, "genie_tools", lambda **_: [])

    def make(route_label: str, order_msgs=(), plan_msgs=()):
        monkeypatch.setattr(router, "chat_model", lambda *a, **k: _model(AIMessage(route_label)))
        monkeypatch.setattr(order_management, "chat_model", lambda *a, **k: _model(*order_msgs))
        monkeypatch.setattr(building_plan, "chat_model", lambda *a, **k: _model(*plan_msgs))
        return agent_module.create_agent_graph("actor-1")

    return make


def _config(session: str) -> dict:
    return {"configurable": {"thread_id": session, "actor_id": "actor-1"}}


@pytest.mark.parametrize(
    ("label", "expected"), [("ORDER", "order_management"), ("plan", "building_plan"), ("?", "building_plan")]
)
def test_router_parses_label(label, expected):
    assert router._parse(label) == expected


@pytest.mark.asyncio
async def test_plan_request_goes_to_building_plan(graph_factory):
    graph = await graph_factory(
        "PLAN",
        plan_msgs=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "estimate_materials",
                        "args": {"project": "paint", "length_ft": 12, "width_ft": 10},
                    }
                ],
            ),
            AIMessage("You need 3 gallons of paint."),
        ],
    )
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "How much paint for a 12x10 room?"}]},
        _config("plan-1"),
    )
    assert result["route"] == "building_plan"
    assert result["messages"][-1].text == "You need 3 gallons of paint."
    tool_results = [m for m in result["messages"] if m.type == "tool"]
    assert tool_results and "paint_gallons" in tool_results[0].text


@pytest.mark.asyncio
async def test_cancel_order_pauses_for_approval_then_resumes(graph_factory, monkeypatch):
    monkeypatch.setitem(
        order_management._ORDERS,
        "HF-1003",
        {**order_management._ORDERS["HF-1003"], "status": "processing"},
    )
    graph = await graph_factory(
        "ORDER",
        order_msgs=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "cancel_order",
                        "args": {"order_id": "HF-1003", "reason": "changed my mind"},
                    }
                ],
            ),
            AIMessage("Order HF-1003 is cancelled."),
        ],
    )
    config = _config("order-1")
    paused = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "Cancel order HF-1003"}]}, config
    )
    assert paused["route"] == "order_management"
    assert paused["__interrupt__"], "cancel_order should pause for approval"
    assert order_management._ORDERS["HF-1003"]["status"] == "processing"

    result = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert order_management._ORDERS["HF-1003"]["status"] == "cancelled"
    assert result["messages"][-1].text == "Order HF-1003 is cancelled."
    # The user's message appears once: the specialist returns only the messages it added.
    assert [m.text for m in result["messages"] if m.type == "human"] == ["Cancel order HF-1003"]


@pytest.mark.asyncio
async def test_run_agent_streams_specialist_steps_once(graph_factory):
    """With subgraph streaming, each specialist message and the interrupt are emitted exactly once."""
    graph = await graph_factory(
        "ORDER",
        order_msgs=[
            AIMessage("", tool_calls=[{"id": "c1", "name": "get_order_status", "args": {"order_id": "HF-1001"}}]),
            AIMessage(
                "",
                tool_calls=[{"id": "c2", "name": "cancel_order", "args": {"order_id": "HF-1003", "reason": "x"}}],
            ),
        ],
    )
    events = [
        e
        async for e in agent_module.run_agent(
            {"messages": [{"role": "user", "content": "Status of HF-1001, then cancel HF-1003"}]},
            session_id="stream-1",
            actor="actor-1",
            graph=graph,
        )
    ]
    updates = [payload for mode, payload in events if mode == "updates"]
    streamed = [m for u in updates for node in u.values() if isinstance(node, dict) for m in node.get("messages", [])]
    ids = [m.id for m in streamed]
    assert len(ids) == len(set(ids)), "a message was streamed twice"
    assert [m.type for m in streamed] == ["ai", "tool", "ai"]
    assert sum("__interrupt__" in u for u in updates) == 1
