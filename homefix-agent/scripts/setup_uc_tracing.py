"""Store this agent's MLflow traces in Unity Catalog tables. Safe to rerun.

Why: the deployed app's network can't reach Databricks cloud storage, where MLflow uploads span
data for experiment-stored traces, so deployed traces showed no spans. With the experiment linked
to a UC schema, MLflow sends spans through the workspace's OTLP API (/api/2.0/otel/v1/traces)
instead. The app needs no code change: with MLFLOW_TRACKING_URI=databricks and
MLFLOW_EXPERIMENT_ID set, MLflow picks up the experiment's UC location at startup.

Steps:
  0. Create the experiment if needed (it must have no traces when it's linked).
  1. Create the trace schema (kept apart from the model-service schema, so tearing those down
     doesn't delete trace history).
  2. Link the experiment to a UC table-prefix location in that schema (MLflow creates the
     spans/logs tables, named with the prefix).
  3. Grant the agent app's service principal USE_SCHEMA, plus MODIFY and SELECT on the tables.

Usage (from homefix-agent/):
  DATABRICKS_CONFIG_PROFILE=agent-dev uv run python scripts/setup_uc_tracing.py \
      [--experiment-name NAME] [--catalog C] [--schema S] [--table-prefix P] [--app NAME]
      [--warehouse-id ID]
Then deploy the agent so the app uses the experiment (and re-reads its trace location).
"""

import argparse
import os
import tomllib
from pathlib import Path

import mlflow
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.catalog import PermissionsChange, Privilege
from mlflow.entities.trace_location import UnityCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_app_name() -> str:
    agent = tomllib.loads((PROJECT_ROOT / "agent.toml").read_text())["agent"]
    return f"agent-bricks-{agent['deployment_name']}"


def _default_experiment_name() -> str | None:
    """The experiment bound with `agentbricks tracing bind` ([tracing] in agent.toml)."""
    return tomllib.loads((PROJECT_ROOT / "agent.toml").read_text()).get("tracing", {}).get(
        "experiment_name"
    )


def _grant(w: WorkspaceClient, securable: str, name: str, principal: str, privileges: list[Privilege]):
    w.grants.update(
        securable_type=securable,
        full_name=name,
        changes=[PermissionsChange(principal=principal, add=privileges)],
    )
    print(f"  granted {', '.join(p.value for p in privileges)} on {securable} {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--experiment-name",
        default=_default_experiment_name(),
        help="Defaults to the [tracing] experiment in agent.toml. Must have no traces yet.",
    )
    parser.add_argument("--catalog", default="agent_workspace_7474653685104191")
    parser.add_argument("--schema", default="homefix_traces")
    parser.add_argument("--table-prefix", default="homefix_agent")
    parser.add_argument("--app", default=_default_app_name(), help="Deployed agent app name")
    parser.add_argument("--warehouse-id", default=os.getenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID"))
    args = parser.parse_args()
    if not args.experiment_name:
        parser.error("--experiment-name is required (no [tracing] binding in agent.toml)")

    w = WorkspaceClient()
    mlflow.set_tracking_uri("databricks")
    full_schema = f"{args.catalog}.{args.schema}"

    # 1. Trace schema
    try:
        w.schemas.get(full_schema)
        print(f"Schema {full_schema} exists")
    except NotFound:
        w.schemas.create(name=args.schema, catalog_name=args.catalog, comment="HomeFix agent MLflow traces")
        print(f"Created schema {full_schema}")

    # 2. Link the experiment
    # A UC trace location can only be linked to an experiment with no traces yet, so create it here
    # (before the app logs anything) rather than letting the first deploy create it.
    experiment = mlflow.get_experiment_by_name(args.experiment_name)
    experiment_id = experiment.experiment_id if experiment else mlflow.create_experiment(args.experiment_name)
    print(f"Experiment {args.experiment_name} ({experiment_id})")

    if args.warehouse_id:
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = args.warehouse_id
    # Creates the tables and links the experiment; a no-op when it's already linked to this prefix.
    mlflow.set_experiment(
        experiment_id=experiment_id,
        trace_location=UnityCatalog(
            catalog_name=args.catalog, schema_name=args.schema, table_prefix=args.table_prefix
        ),
    )
    location = mlflow.get_experiment(experiment_id).trace_location
    print(f"Experiment {experiment_id} stores traces in {location}")
    tables = [
        t
        for t in (
            getattr(location, "full_otel_spans_table_name", None),
            getattr(location, "full_otel_logs_table_name", None),
        )
        if t
    ]
    print(f"  tables: {', '.join(tables)}")

    # 3. Grants for the app's service principal
    principal = w.apps.get(args.app).service_principal_client_id
    print(f"Granting app {args.app} (service principal {principal})")
    _grant(w, "schema", full_schema, principal, [Privilege.USE_SCHEMA])
    for table in tables:
        _grant(w, "table", table, principal, [Privilege.MODIFY, Privilege.SELECT])
    print("Done. Deploy the agent so the app picks up the experiment and its trace location.")


if __name__ == "__main__":
    main()
