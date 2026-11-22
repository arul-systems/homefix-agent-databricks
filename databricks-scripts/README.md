# create-model-service

Databricks Asset Bundle that creates AI Gateway resources for HomeFix. Everything lives in the
Unity Catalog schema `agent_workspace_7474653685104191.homefix_ai` and shows up under **AI Gateway**.

| Resource | Type | Backed by |
|---|---|---|
| `openai-api-key` | UC secret | `OPENAI_API_KEY` from `.env` |
| `openai` | Model provider service | OpenAI, key from the UC secret |
| `homefix-gpt-6-sol` | Model service | `gpt-6-sol` through the `openai` provider |
| `homefix-llama-3-1-8b-instruct` | Model service | `system.ai.meta_llama_v3_1_8b_instruct` (Databricks-hosted) |
| `homefix-llama-3-3-70b-instruct` | Model service | `system.ai.llama_v3_3_70b_instruct` (Databricks-hosted) |

## Deploy

```bash
cp .env.example .env        # set DATABRICKS_CONFIG_PROFILE and OPENAI_API_KEY
./scripts/deploy.sh
```

`deploy.sh` loads `.env`, passes the key to the bundle as `BUNDLE_VAR_openai_api_key`, then runs
`databricks bundle validate` + `deploy`. Rerun it to rotate the key or apply changes.

## Access for the deployed agent

The agent app runs as its own service principal, so `deploy.sh` grants it `USE_SCHEMA` on the
schema and `EXECUTE` on each model service. It finds the principal from `AGENT_APP_NAME` in `.env`
(default `agent-bricks-list`). Deploy the agent app first, then run `deploy.sh`. If the app is
recreated, it gets a new service principal, so run `deploy.sh` again.

## Tear down

`./scripts/undeploy.sh` deletes the model services, provider, secret and schema
(prompts for confirmation; pass `-y` to skip).

## Notes
- Names, the OpenAI model, catalog and schema are bundle variables in `databricks.yml`; override
  with `--var name=value`, `BUNDLE_VAR_name`, or the optional keys in `.env.example`.
- Rate limits (per minute, set in `databricks.yml`):

  | Model service | Per user | Whole service |
  |---|---|---|
  | GPT 6 sol | 20 requests, 20k tokens | 100 requests, 100k tokens |
  | Llama 3.1 8B / 3.3 70B | 60 requests, 60k tokens | 300 requests, 300k tokens |

  Change them with e.g. `--var gpt_user_requests_per_min=50` or by editing the defaults. Requests over a limit get HTTP 429.
- The Llama model services use pay-per-token destinations; this workspace doesn't allow
  provisioned throughput.
