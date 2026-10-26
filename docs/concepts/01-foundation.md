# Phase 1: foundation concepts

## Resource organisation (AZ-305: governance)

| Concept | What we do | Why |
|---|---|---|
| **Resource group** | Everything goes in `rg-house-agents` | One lifecycle: deleting the RG removes every resource |
| **Tags** | `project=house-agents`, `owner=mushtaque`, `env=dev` | Cost reports and policies can filter by tag |
| **Budget** | Alert at INR 1,000 per month | You get an email before costs surprise you (lesson from the INR 1,600 load-test day) |
| **Region** | `uaenorth` for data and apps | Close to you; the Foundry model is in `eastus`, so each LLM call crosses regions (visible later in traces) |

## Azure SQL Database serverless

- **Serverless** compute scales vCores automatically between a min and max, and **auto-pauses** after an idle period. While paused you pay only for storage. The first query after a pause takes roughly a minute to resume.
- **Provisioned** compute bills every hour, whether used or not. It suits steady production traffic.
- Architect's rule: **serverless for intermittent dev/test or spiky workloads; provisioned for steady ones**.

## Authentication: Entra ID vs SQL logins

| | SQL authentication | Microsoft Entra ID |
|---|---|---|
| Credential | Username + password stored somewhere | Identity token (user, group or managed identity) |
| Rotation | Manual | Handled by Entra |
| MFA / conditional access | No | Yes |
| Apps in Azure | Password in config or Key Vault | **Managed identity: no secret at all** |

We set **Entra-only authentication**:
- **You** are the server admin.
- The **agent's managed identity** becomes a database user later, in Phase 3.

## Least privilege

The agent only needs to **read** `dbo.house_prices`. Its database user gets `db_datareader` and nothing else. Even if a prompt-injection attack convinces the LLM to write `DROP TABLE`, the database refuses it. This is defence in depth: the SQL guard in code is layer 1, and database permissions are layer 2.

## Portal walkthrough: create Azure SQL

1. Portal → **Create a resource** → **SQL Database**.
2. **Basics:**
   - Subscription: Azure Basic. Resource group: **Create new → `rg-house-agents`**.
   - Database name: `housepricedb`.
   - Server: **Create new**:
     - Name: `sql-house-agents`. It must be globally unique; add digits if it's taken.
     - Location: **(Middle East) UAE North**.
     - Authentication method: **Use Microsoft Entra-only authentication**.
     - Set admin: **select yourself**.
   - Want to use SQL elastic pool: **No**.
   - Workload environment: **Development**.
   - Compute + storage: **Configure database**:
     - Service tier: **General Purpose**.
     - Compute tier: **Serverless**.
     - Max vCores: **1**, min vCores: **0.5**.
     - Auto-pause delay: **1 hour**.
     - Data max size: **1 GB**.
     - Backup storage redundancy: **Locally-redundant** (cheapest).
3. **Networking:**
   - Connectivity method: **Public endpoint**.
   - Allow Azure services and resources to access this server: **Yes**. Container Apps need this until we move to private endpoints.
   - Add current client IP address: **Yes**.
4. **Additional settings:** Use existing data **None**. We load it with a script.
5. **Tags:** `project=house-agents`, `owner=mushtaque`, `env=dev`.
6. **Review + create**. It takes about 3–5 minutes.

Then run `python infra/scripts/sql_setup.py --load`. It creates the table, loads the 10,000 rows and verifies the Downtown average is 770,570.
