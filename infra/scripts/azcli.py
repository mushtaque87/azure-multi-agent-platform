"""Shared helpers for deploy.py, loadtest_setup.py and apim_setup.py.

Everything shells out to the `az` CLI (no Azure SDK needed). Resource names
(never keys) are remembered in .deploy_state.json so the scripts can find
each other's resources.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, ".deploy_state.json")


def az(*args, parse=True):
    """Run an az command; return parsed JSON (or None). Raise on failure."""
    cmd = ["az", *args, "-o", "json" if parse else "none", "--only-show-errors"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"`az {' '.join(args[:4])} ...` failed:\n{r.stderr.strip()[-2000:]}")
    out = r.stdout.strip().lstrip("\ufeff")  # some APIM responses start with a UTF-8 BOM
    if not (parse and out):
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out  # e.g. `az rest` on APIM policies can return non-JSON bodies


def exists(*args):
    """True if an az `show`-style command succeeds."""
    r = subprocess.run(["az", *args, "-o", "none", "--only-show-errors"], capture_output=True)
    return r.returncode == 0


def retry(fn, what, attempts=10, wait=15):
    """Retry fn() - used for role assignments, which take a minute or two to propagate."""
    for i in range(1, attempts + 1):
        try:
            return fn()
        except RuntimeError as e:
            if i == attempts:
                raise
            print(f"   ...{what} not ready yet (attempt {i}/{attempts}), retrying in {wait}s")
            if "--debug" in sys.argv:
                print(e)
            time.sleep(wait)


def step(msg):
    print(f"\n==> {msg}", flush=True)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def require_state(*keys):
    state = load_state()
    missing = [k for k in keys if k not in state]
    if missing:
        sys.exit(f"Missing {missing} in {STATE_FILE}. Run deploy.py first.")
    return state


def ensure_role(principal_id, role, scope, principal_type="ServicePrincipal"):
    """Grant an RBAC role if it isn't already assigned."""
    current = az("role", "assignment", "list", "--assignee", principal_id,
                 "--role", role, "--scope", scope)
    if current:
        print(f"   role '{role}' already assigned")
        return
    az("role", "assignment", "create", "--assignee-object-id", principal_id,
       "--assignee-principal-type", principal_type, "--role", role, "--scope", scope, parse=False)
    print(f"   granted '{role}'")


def set_secret(vault, name, value):
    """Store a secret via a temp file so the value never appears on a command line."""
    path = os.path.join(HERE, f".tmp_{name}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value)
        return az("keyvault", "secret", "set", "--vault-name", vault, "-n", name,
                  "--file", path, "--encoding", "utf-8")
    finally:
        os.remove(path)


def workspace_vault(rg, ws):
    """Name and resource ID of the Key Vault Azure ML created with the workspace."""
    vault_id = az("ml", "workspace", "show", "-n", ws, "-g", rg, "--query", "key_vault")
    return vault_id.rsplit("/", 1)[-1], vault_id


def grant_vault(vault, vault_id, principal_id, write=False, principal_type="ServicePrincipal"):
    """Let a principal read (or read+write) secrets, whichever permission model the vault uses."""
    if az("keyvault", "show", "-n", vault, "--query", "properties.enableRbacAuthorization"):
        role = "Key Vault Secrets Officer" if write else "Key Vault Secrets User"
        ensure_role(principal_id, role, vault_id, principal_type)
    else:
        perms = ["get", "list", "set"] if write else ["get"]
        az("keyvault", "set-policy", "-n", vault, "--object-id", principal_id,
           "--secret-permissions", *perms, parse=False)
        print(f"   access policy set ({'/'.join(perms)})")


def my_object_id():
    return az("ad", "signed-in-user", "show", "--query", "id")
