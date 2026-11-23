# HomeFix Agent on Databricks

A reference architecture for building a **custom agent on Databricks** (Agent Bricks / AgentKit on
Databricks Apps) that calls models through **Databricks AI Gateway** (Unity Catalog model services),
with a separate web frontend.

HomeFix is a sample assistant for a home-improvement store. It answers order questions (status,
listing, cancellation with human approval) and helps customers plan projects (material quantities,
cost estimates, permit guidance).

## Architecture

![HomeFix architecture](docs/architecture.png)

The diagram source is [`docs/architecture.d2`](docs/architecture.d2) ([D2](https://d2lang.com)). After
editing it, regenerate the PNG from the repo root:


### Reference architecture

| Layer | What this repo uses | Why |
| --- | --- | --- |
| Frontend | `agent-ui/`, Next.js app router, hosted on AWS Amplify | Keeps the Databricks credentials on the server; the browser only talks to `/chat`. |
| Auth to the agent | Databricks service principal, OAuth client credentials | Machine-to-machine access; the principal needs `CAN_USE` on the agent app. |
| Agent runtime | `homefix-agent/`, a custom agent on Databricks Apps (Agent Bricks CLI + `databricks_agentkit`) | Durable invocations, streaming, session and memory stores, crash recovery. |
| Orchestration | LangGraph `StateGraph`: a router node, then one specialist node per domain; each specialist is a `create_agent` subgraph with its own tools | Cheap, fast routing on a small model; bigger models only where needed. |
| Human in the loop | `HumanInTheLoopMiddleware` on `cancel_order` | Destructive actions pause until a person approves or rejects them. |
| Model access | AI Gateway model services and a model provider service (Unity Catalog objects), created by `databricks-scripts/` (Databricks Asset Bundle) | One governed place for model access: UC grants, rate limits, external-provider secrets in UC. |
| Observability | MLflow 3 tracing into Unity Catalog tables | The deployed app can't reach the cloud storage MLflow uses for experiment-stored spans. |

## Repository layout

| Folder | Contents |
| --- | --- |
| [`databricks-scripts/`](databricks-scripts/README.md) | Bundle that creates the UC schema, OpenAI secret, model provider service, the three model services with rate limits, and the grants. `scripts/deploy.sh` and `scripts/undeploy.sh`. |
| [`homefix-agent/`](homefix-agent/README.md) | The custom agent (API only): graph in `agent/agent.py`, one file per node with its tools in `agent/node/`. |
| [`agent-ui/`](agent-ui/README.md) | Next.js chat frontend with streaming replies and approve/reject for gated actions. |
| `amplify.yml` | Amplify build spec for `agent-ui` (writes the server-side `.env` from Amplify environment variables). |

## Getting started

1. **Deploy the agent:** in `homefix-agent/`, run `agentbricks --profile <profile> deploy list --source .`.
2. **Create the model services:** in `databricks-scripts/`, fill in `.env` (OpenAI key, profile,
   `AGENT_UI_CLIENT_ID`) and run `scripts/deploy.sh`. It grants the agent app's service principal
   access to the model services and the frontend's service principal `CAN_USE` on the agent app.
   Run it after the agent app exists, and again whenever the app is recreated.
3. **Run the frontend:** in `agent-ui/`, copy `.env.example` to `.env.local`, fill it in, and run
   `npm install && npm run dev`, or deploy it to Amplify with `amplify.yml`.

## Callouts and known gaps

This is a reference implementation, not a production system. Review these before using it with real
customers.

- **No guardrails.** Nothing detects or masks sensitive data (PII, card numbers), filters unsafe
  content, or blocks prompt injection on any model call. Unity Catalog model services have no
  guardrail configuration in the API, CLI, SDK, or bundles; AI Gateway service policies (for example
  Sensitive Data Detection) are in Beta and can only be attached in the UI, and none are attached
  here. Anything a user types reaches the models as is.
- **No end-user authentication in the frontend.** `agent-ui` has no login. Anyone who can reach it
  uses the agent through one shared service principal, so the agent can't tell users apart (memory
  and sessions aren't per user), and usage counts against that principal's rate limits. Put it
  behind real auth (at least Amplify access control) before sharing the URL.
- **Rate limits are the only usage control.** Per-user and per-service request and token limits are
  set on each model service; there are no budgets or spend alerts.
- **Sample data and tools.** Orders are an in-memory dictionary (shared by everyone, reset on
  restart). Cost, material, and permit answers come from rule-of-thumb tables, not real pricing or
  local building codes.
- **Narrow human approval.** Only `cancel_order` requires approval. Any new tool with side effects
  needs its own entry in the node's `REQUIRE_APPROVAL`.
- **Model quality.** Llama 3.3 70B sometimes writes a tool call as text (`<function=...>`) instead of
  calling the tool. The router on Llama 3.1 8B returns a free-text label, and anything that isn't
  "ORDER" goes to the building-plan specialist.
- **Token usage.** Traces show much higher input-token counts than the prompts explain (tens of
  thousands of tokens per question). This hasn't been investigated, and it affects both cost and
  rate limits.
- **Permissions reset when the agent app is recreated.** A new app gets a new service principal and
  an empty permission list; rerun `databricks-scripts/scripts/deploy.sh` to restore the grants.
- **Secrets handling.** The OpenAI key lives in a Unity Catalog secret. On Amplify, the service
  principal secret is written into the server build's `.env`, so it is stored with the build
  artifacts (never sent to the browser). A secrets manager read at runtime is safer.
- **Not verified on Amplify.** Response streaming and long agent turns (20 s or more) haven't been
  tested on Amplify's server-side compute; replies may arrive all at once or time out.
- **Workspace-specific defaults.** Catalog, schema, profile, and app names are hard-coded defaults
  for one workspace; override them through `.env` and bundle variables for another workspace.
