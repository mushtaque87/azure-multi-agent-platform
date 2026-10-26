# Tools image: Python + Microsoft ODBC Driver 18, for running infra/scripts that talk to Azure SQL.
# The same driver install is reused by the agent image on Container Apps.
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc-dev \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pyodbc pandas azure-identity

WORKDIR /work
