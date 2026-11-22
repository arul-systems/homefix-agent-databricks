"""Building plan node: help planning home projects - materials, costs, permits, steps.

Model: ``homefix-gpt-6-sol``. The tools below use simple rule-of-thumb tables; replace them with
real pricing and local permit data as needed.
"""

import math
from typing import Any, Literal

from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from agent.common import BUILDING_PLAN_MODEL, chat_model, run_specialist

SYSTEM_PROMPT = """You are the HomeFix building plan assistant.
Help customers plan home-improvement projects: scope, step-by-step instructions, materials,
quantities, cost estimates, and permits.
Use the tools for quantities, costs, and permit guidance instead of guessing numbers, and say that
costs are estimates. Ask for dimensions when you need them. Flag safety issues (structural,
electrical, gas) and recommend a licensed professional where appropriate."""

Project = Literal["paint", "flooring", "tile", "drywall", "deck"]
Quality = Literal["budget", "standard", "premium"]

# Material cost per sq ft by quality tier, plus typical labor per sq ft if hired out.
_COST_PER_SQFT: dict[str, dict[str, float]] = {
    "paint": {"budget": 0.5, "standard": 0.9, "premium": 1.5, "labor": 2.0},
    "flooring": {"budget": 2.0, "standard": 4.5, "premium": 9.0, "labor": 3.0},
    "tile": {"budget": 2.5, "standard": 6.0, "premium": 15.0, "labor": 8.0},
    "drywall": {"budget": 0.6, "standard": 0.9, "premium": 1.3, "labor": 1.8},
    "deck": {"budget": 15.0, "standard": 25.0, "premium": 45.0, "labor": 20.0},
}

_PERMITS: dict[str, str] = {
    "paint": "No permit needed for interior or exterior painting in most areas. Check HOA rules.",
    "flooring": "Usually no permit for replacing floor coverings. Structural subfloor work may need one.",
    "tile": "Usually no permit for tile. Moving plumbing in a bathroom remodel typically needs one.",
    "drywall": "Small repairs need no permit. Replacing large areas or work exposing wiring often does.",
    "deck": "Most areas require a permit for decks over 200 sq ft, over 30 in. above grade, or "
    "attached to the house. Expect a footing and a final inspection.",
}


def _area(project: str, length_ft: float, width_ft: float, height_ft: float) -> float:
    # Walls (paint, drywall): perimeter x height. Floors and decks: length x width.
    if project in ("paint", "drywall"):
        return 2 * (length_ft + width_ft) * height_ft
    return length_ft * width_ft


@tool
def estimate_materials(
    project: Project, length_ft: float, width_ft: float, height_ft: float = 8.0
) -> dict[str, Any]:
    """Estimate material quantities for a room or deck from its dimensions in feet.

    For paint and drywall the area is the room's walls (perimeter x height); for flooring, tile,
    and decks it's length x width. Includes a waste allowance.
    """
    area = _area(project, length_ft, width_ft, height_ft)
    if project == "paint":
        # ~350 sq ft per gallon per coat, two coats.
        items = {"paint_gallons": math.ceil(area * 2 / 350), "primer_gallons": math.ceil(area / 300)}
    elif project == "drywall":
        items = {"drywall_sheets_4x8": math.ceil(area * 1.10 / 32), "joint_compound_buckets": math.ceil(area / 500)}
    elif project == "flooring":
        items = {"flooring_sqft": math.ceil(area * 1.10), "underlayment_sqft": math.ceil(area)}
    elif project == "tile":
        items = {"tile_sqft": math.ceil(area * 1.15), "thinset_bags_50lb": math.ceil(area / 90), "grout_bags_25lb": math.ceil(area / 100)}
    else:  # deck
        items = {"deck_boards_16ft": math.ceil(area * 1.10 / (16 * 0.47)), "joists_2x8x12": math.ceil(width_ft / 1.33) + 1, "screws_lb": math.ceil(area / 100) * 5}
    return {"project": project, "area_sqft": round(area, 1), "materials": items}


@tool
def estimate_cost(project: Project, area_sqft: float, quality: Quality = "standard") -> dict[str, Any]:
    """Estimate project cost (materials, and labor if hired out) for an area in square feet."""
    rates = _COST_PER_SQFT[project]
    materials = area_sqft * rates[quality]
    labor = area_sqft * rates["labor"]
    return {
        "project": project,
        "quality": quality,
        "materials_usd": round(materials, 2),
        "labor_usd_if_hired": round(labor, 2),
        "diy_total_usd": round(materials, 2),
        "hired_total_usd": round(materials + labor, 2),
        "note": "Rough national averages; local prices vary.",
    }


@tool
def permit_requirements(project: Project) -> str:
    """General permit guidance for a project type. Always confirm with the local building department."""
    return _PERMITS[project] + " Confirm with your local building department before starting."


TOOLS: list[BaseTool] = [estimate_materials, estimate_cost, permit_requirements]

REQUIRE_APPROVAL: dict[str, bool] = {}


def build(shared_tools: list[BaseTool]):
    """Build this node's graph node, given tools shared by every specialist (memory, Genie)."""
    agent = create_agent(
        # gpt-6-sol rejects function tools with reasoning on the chat-completions API (which AI
        # Gateway uses here), so reasoning is turned off for this node.
        model=chat_model(BUILDING_PLAN_MODEL, extra_params={"reasoning_effort": "none"}),
        tools=[*TOOLS, *shared_tools],
        system_prompt=SYSTEM_PROMPT,
        name="building_plan_agent",
    )

    async def building_plan(state: dict, config: RunnableConfig) -> dict:
        return await run_specialist(agent, state, config)

    return building_plan
