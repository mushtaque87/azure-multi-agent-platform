"""Deploy the multi-agent stack to Azure Container Apps (Phase 4). Idempotent: rerun any time.

    python infra/scripts/aca_multiagent_deploy.py                 # create/update everything
    python infra/scripts/aca_multiagent_deploy.py --image mushtaque87/house-agent:v3   # roll out a new image

Layout in environment cae-house-agents (one image, the role picked by AGENT_ROLE):
  ca-phoenix       public ingress :6006, login enabled, min=max 1 replica (created in the portal; checked here)
  ca-valuation     INTERNAL ingress :8000, own managed identity        -> Foundry + SQL (db_datareader)
  ca-market        INTERNAL ingress :8000, own managed identity        -> Foundry + SQL (db_datareader)
  acrhouseagents   PUBLIC ingress  :8000, supervisor + chat UI         -> Foundry only, NO data tools

For every agent identity:
  - "Cognitive Services OpenAI User" on the Foundry resource (keyless LLM)
  - "Key Vault Secrets User" on kv-house-agents, so the app can resolve the phoenix-api-key Key Vault reference
  - env PHOENIX_COLLECTOR_ENDPOINT=http://ca-phoenix (internal), PHOENIX_API_KEY=secretref:phoenix-api-key
Afterwards (needs ODBC, so it runs in the tools container), make the specialists read-only DB users:
  ./infra/scripts/run_tools.sh sql_setup.py -s sql-house-agents --reader ca-valuation
  ./infra/scripts/run_tools.sh sql_setup.py -s sql-house-agents --reader ca-market
"""
import argparse

from azcli import az, ensure_role, exists, load_state, retry, save_state, step

RG = "rg-house-agents"
ENV = "cae-house-agents"
VAULT = "kv-house-agents"
FOUNDRY_RG, FOUNDRY_ACCOUNT = "rg_az_ai103", "ai103-agenticsystem-1"
SQL = ["SQL_SERVER=sql-house-agents.database.windows.net", "SQL_DATABASE=housepricedb"]
PHOENIX = "ca-phoenix"
SUPERVISOR = "acrhouseagents"
SPECIALISTS = {"ca-valuation": "valuation", "ca-market": "market"}


def phoenix_env():
    # Internal address (http://<app-name>): trace traffic stays inside the environment
    return [f"PHOENIX_COLLECTOR_ENDPOINT=http://{PHOENIX}", "PHOENIX_API_KEY=secretref:phoenix-api-key"]


def wire_identity_and_secrets(app, foundry_id, vault_id, vault_uri):
    """Identity -> RBAC (Foundry + Key Vault) -> Key Vault reference secret. Returns principalId."""
    principal = az("containerapp", "identity", "assign", "-g", RG, "-n", app, "--system-assigned")["principalId"]
    ensure_role(principal, "Cognitive Services OpenAI User", foundry_id)
    ensure_role(principal, "Key Vault Secrets User", vault_id)
    # The secret's VALUE is never in this script: Container Apps fetches it from Key Vault with the app's identity.
    # Retried because new role assignments take a minute or two to propagate.
    retry(lambda: az("containerapp", "secret", "set", "-g", RG, "-n", app, "--secrets",
                     f"phoenix-api-key=keyvaultref:{vault_uri}secrets/phoenix-api-key,identityref:system"),
          "Key Vault access for the app identity", attempts=12, wait=20)
    return principal


def main():
    state = load_state()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image", default=state.get("aca_image_v2", "docker.io/mushtaque87/house-agent:v2"))
    image = p.parse_args().image

    foundry_id = az("cognitiveservices", "account", "show", "-g", FOUNDRY_RG, "-n", FOUNDRY_ACCOUNT, "--query", "id")
    vault = az("keyvault", "show", "-n", VAULT, "--query", "{id:id, uri:properties.vaultUri}")

    step(f"0. Phoenix ({PHOENIX}) must exist with public ingress")
    if not exists("containerapp", "show", "-g", RG, "-n", PHOENIX):
        raise SystemExit(f"{PHOENIX} not found - create it first (portal steps in docs)")
    ph = az("containerapp", "show", "-g", RG, "-n", PHOENIX, "--query",
            "{external:properties.configuration.ingress.external, fqdn:properties.configuration.ingress.fqdn}")
    if not ph["external"]:
        az("containerapp", "ingress", "enable", "-g", RG, "-n", PHOENIX, "--type", "external",
           "--target-port", "6006", "--transport", "auto")
    print(f"   https://{ph['fqdn'].replace('.internal.', '.')}")

    principals = {}
    for app, role in SPECIALISTS.items():
        step(f"1. Specialist {app} (AGENT_ROLE={role}, internal only)")
        base_env = [f"AGENT_ROLE={role}", f"PUBLIC_URL=http://{app}", *SQL]
        if not exists("containerapp", "show", "-g", RG, "-n", app):
            az("containerapp", "create", "-g", RG, "-n", app, "--environment", ENV, "--image", image,
               "--ingress", "internal", "--target-port", "8000", "--cpu", "0.5", "--memory", "1Gi",
               "--min-replicas", "0", "--max-replicas", "1", "--system-assigned", "--env-vars", *base_env)
        principals[app] = wire_identity_and_secrets(app, foundry_id, vault["id"], vault["uri"])
        az("containerapp", "update", "-g", RG, "-n", app, "--image", image,
           "--set-env-vars", *base_env, *phoenix_env())
        print(f"   principalId {principals[app]}")

    step(f"2. Supervisor {SUPERVISOR} (public; discovers specialists via Agent Cards)")
    principals[SUPERVISOR] = wire_identity_and_secrets(SUPERVISOR, foundry_id, vault["id"], vault["uri"])
    specialist_urls = ",".join(f"http://{a}" for a in SPECIALISTS)
    az("containerapp", "update", "-g", RG, "-n", SUPERVISOR, "--image", image,
       "--set-env-vars", "AGENT_ROLE=supervisor", f"SPECIALIST_URLS={specialist_urls}", *phoenix_env())
    fqdn = az("containerapp", "show", "-g", RG, "-n", SUPERVISOR, "--query", "properties.configuration.ingress.fqdn")

    state.update(aca_image_v2=image, aca_principals=principals, aca_url=f"https://{fqdn}",
                 phoenix_url=f"https://{ph['fqdn'].replace('.internal.', '.')}")
    save_state(state)

    step("3. Read-only SQL users for the specialists (run these next; the supervisor needs none)")
    for app in SPECIALISTS:
        print(f"   ./infra/scripts/run_tools.sh sql_setup.py -s sql-house-agents --reader {app}")
    print(f"\nChat UI: https://{fqdn}   Phoenix: {state['phoenix_url']}")


if __name__ == "__main__":
    main()
