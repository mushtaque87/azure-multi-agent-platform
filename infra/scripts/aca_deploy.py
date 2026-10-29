"""Deploy the agent to Azure Container Apps - reproduces the manual portal setup of Phase 3.

    python infra/scripts/aca_deploy.py                    # create or update everything (idempotent)
    python infra/scripts/aca_deploy.py --image mushtaque87/house-agent:v2   # roll out a new image
    python infra/scripts/aca_deploy.py --cleanup --yes    # delete the app and its environment

What it does:
  1. Container Apps environment (Consumption, no VNet) with its Log Analytics workspace
  2. Container App: public Docker Hub image, external ingress on 8000, scale 0..1, env vars
  3. System-assigned managed identity
  4. RBAC: identity -> "Cognitive Services OpenAI User" on the Foundry resource (keyless LLM access)
  5. Prints the step that makes the identity a read-only SQL user (runs in the ODBC tools container)

Build and push the image first (linux/amd64, because Container Apps runs x86):
    docker buildx build --platform linux/amd64 -t mushtaque87/house-agent:v1 --push agent/
"""
import argparse
import sys

from azcli import az, ensure_role, exists, load_state, save_state, step

FOUNDRY_RG = "rg_az_ai103"
FOUNDRY_ACCOUNT = "ai103-agenticsystem-1"


def parse_args(state):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-g", "--resource-group", default=state.get("resource_group", "rg-house-agents"))
    p.add_argument("-l", "--location", default=state.get("location", "uaenorth"))
    p.add_argument("--environment", default=state.get("aca_environment", "cae-house-agents"))
    p.add_argument("--app", default=state.get("aca_app", "acrhouseagents"))
    p.add_argument("--image", default=state.get("aca_image", "docker.io/mushtaque87/house-agent:v1"))
    p.add_argument("--sql-server", default=state.get("sql_server", "sql-house-agents"))
    p.add_argument("--sql-database", default=state.get("sql_database", "housepricedb"))
    p.add_argument("--cleanup", action="store_true", help="delete the app and the environment")
    p.add_argument("--yes", action="store_true", help="confirm --cleanup")
    return p.parse_args()


def main():
    state = load_state()
    a = parse_args(state)
    rg = a.resource_group

    if a.cleanup:
        if not a.yes:
            sys.exit("Refusing to delete without --yes")
        step(f"Deleting Container App {a.app} and environment {a.environment}")
        if exists("containerapp", "show", "-g", rg, "-n", a.app):
            az("containerapp", "delete", "-g", rg, "-n", a.app, "--yes", parse=False)
        if exists("containerapp", "env", "show", "-g", rg, "-n", a.environment):
            az("containerapp", "env", "delete", "-g", rg, "-n", a.environment, "--yes", parse=False)
        print("   done (SQL database user remains; drop it with DROP USER if the app is gone for good)")
        return

    step(f"1. Container Apps environment {a.environment}")
    if exists("containerapp", "env", "show", "-g", rg, "-n", a.environment):
        print("   exists")
    else:
        # Creates a Log Analytics workspace automatically; Consumption plan bills per use, no VNet
        az("containerapp", "env", "create", "-g", rg, "-n", a.environment, "-l", a.location)

    env_vars = [f"SQL_SERVER={a.sql_server}.database.windows.net", f"SQL_DATABASE={a.sql_database}"]
    step(f"2. Container App {a.app} ({a.image})")
    if exists("containerapp", "show", "-g", rg, "-n", a.app):
        # Update = new revision with the given image/env; traffic moves when it is healthy
        az("containerapp", "update", "-g", rg, "-n", a.app, "--image", a.image,
           "--set-env-vars", *env_vars, "--min-replicas", "0", "--max-replicas", "1")
    else:
        az("containerapp", "create", "-g", rg, "-n", a.app, "--environment", a.environment,
           "--image", a.image, "--target-port", "8000", "--ingress", "external",
           "--cpu", "0.5", "--memory", "1Gi", "--min-replicas", "0", "--max-replicas", "1",
           "--env-vars", *env_vars)

    step("3. System-assigned managed identity")
    ident = az("containerapp", "identity", "assign", "-g", rg, "-n", a.app, "--system-assigned")
    principal_id = ident["principalId"]
    print(f"   principalId {principal_id}")

    step("4. Keyless Foundry access: Cognitive Services OpenAI User")
    foundry_id = az("cognitiveservices", "account", "show", "-g", FOUNDRY_RG, "-n", FOUNDRY_ACCOUNT,
                    "--query", "id")  # --query on a single field returns a JSON string
    ensure_role(principal_id, "Cognitive Services OpenAI User", foundry_id)

    fqdn = az("containerapp", "show", "-g", rg, "-n", a.app,
              "--query", "properties.configuration.ingress.fqdn")
    state.update(aca_environment=a.environment, aca_app=a.app, aca_image=a.image,
                 aca_principal_id=principal_id, aca_url=f"https://{fqdn}")
    save_state(state)

    step("5. Read-only SQL user for the identity (needs ODBC, so it runs in the tools container)")
    print(f"   ./infra/scripts/run_tools.sh sql_setup.py -s {a.sql_server} --reader {a.app}")
    print(f"\nApp: https://{fqdn}   (role assignments can take a few minutes to apply)")


if __name__ == "__main__":
    main()
