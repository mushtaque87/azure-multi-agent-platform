"""The agent's tools. Each has a Pydantic schema (validated BEFORE any I/O) and a precise description,
because the LLM chooses tools by reading their names, descriptions and argument schemas."""
import json
import os
from functools import lru_cache  # cache a function's result: load the model from disk only once
from typing import Literal         # "one of these exact strings" - becomes an enum in the tool schema

import httpx                                  # HTTP client for the Azure ML endpoint
from langchain_core.tools import tool          # @tool turns a function into something the LLM can call
from pydantic import BaseModel, Field          # argument schemas with validation rules

from . import config, db
from .guards import GuardError, validate_sql

# Column order the Azure ML endpoint (score.py) expects
FEATURES = ["SQFT", "BEDROOMS", "BATHROOMS", "AGE_YEARS", "GARAGE_SPACES",
            "NEIGHBORHOOD", "CONDITION", "HAS_POOL", "LOT_SIZE_SQFT"]
Neighborhood = Literal["Downtown", "Suburban", "Rural", "Waterfront", "Urban"]
Condition = Literal["Excellent", "Good", "Fair", "Poor"]


# Tool argument rules. The LLM sees these ranges in the tool schema, AND Pydantic enforces them before
# any model/DB call: sqft=50 or neighborhood="Mars" is rejected and the error goes back to the LLM.
# Ranges match the training data - predicting outside it would be unreliable extrapolation.
class HouseFeatures(BaseModel):
    sqft: int = Field(ge=500, le=5000, description="Living area in square feet (500-5000)")
    bedrooms: int = Field(ge=1, le=6, description="Number of bedrooms (1-6)")
    bathrooms: int = Field(ge=1, le=4, description="Number of bathrooms (1-4)")
    age_years: int = Field(ge=0, le=50, description="Age of the house in years (0-50)")
    garage_spaces: int = Field(ge=1, le=10, description="Garage spaces (1-10)")
    neighborhood: Neighborhood
    condition: Condition
    has_pool: bool
    lot_size_sqft: int = Field(ge=1000, le=20000, description="Lot size in square feet (1000-20000)")

    def as_row(self):  # values in FEATURES order
        return [self.sqft, self.bedrooms, self.bathrooms, self.age_years, self.garage_spaces,
                self.neighborhood, self.condition, self.has_pool, self.lot_size_sqft]


@lru_cache(maxsize=1)
def _local_model():
    """Load the trained model + encoder (copied from ai-mlops/azure_ml/model) once, then reuse."""
    import joblib  # imported lazily: only needed when the local model is used
    return (joblib.load(os.path.join(config.LOCAL_MODEL_DIR, "house_price_model.joblib")),
            joblib.load(os.path.join(config.LOCAL_MODEL_DIR, "ordinal_encoder.joblib")))


def _predict(features: HouseFeatures) -> tuple[float, str]:
    """Azure ML endpoint if configured, else the same model loaded locally. Returns (price, source)."""
    if config.ML_ENDPOINT_URL:  # production path: call the managed endpoint over HTTPS
        body = {"input_data": {"columns": FEATURES, "data": [features.as_row()]}}
        r = httpx.post(config.ML_ENDPOINT_URL, json=body, timeout=30,
                       headers={"Authorization": f"Bearer {config.ML_ENDPOINT_KEY}"})
        r.raise_for_status()  # HTTP 4xx/5xx -> exception -> handled by the caller
        return float(r.json()["predictions"][0]), "azure-ml-endpoint"
    # Local path: same preprocessing as score.py, so results are identical to the endpoint
    import pandas as pd
    model, encoder = _local_model()
    row = dict(zip(FEATURES, features.as_row()))
    # Text categories -> the numbers the model was trained on (e.g. "Downtown" -> 0.0)
    row["NEIGHBORHOOD"], row["CONDITION"] = encoder.transform([[row["NEIGHBORHOOD"], row["CONDITION"]]])[0]
    row["HAS_POOL"] = int(row["HAS_POOL"])
    # Column order used at training time (different from the endpoint's input order)
    order = ["SQFT", "BEDROOMS", "BATHROOMS", "AGE_YEARS", "GARAGE_SPACES",
             "LOT_SIZE_SQFT", "NEIGHBORHOOD", "CONDITION", "HAS_POOL"]
    return float(model.predict(pd.DataFrame([row])[order].astype(float))[0]), "local-model"


# The docstring below is the tool DESCRIPTION the LLM reads to decide when to use it.
@tool(args_schema=HouseFeatures)
def predict_price(**kwargs) -> str:
    """Predict the sale price of a house that is NOT in the dataset, from its 9 features.
    Use when the user asks what a house would sell for, its value, or its estimated price.
    All 9 features are required; if any are missing, ask the user for them instead of guessing."""
    try:
        price, source = _predict(HouseFeatures(**kwargs))
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Valuation service unavailable: {type(e).__name__}"})
    return json.dumps({"predicted_price_usd": round(price, 2), "source": source})


@tool
def explain_prediction() -> str:
    """Explain which house features drive the price model most (global feature importance).
    Use when the user asks why a price is what it is, or what affects house prices."""
    model, _ = _local_model()
    names = ["SQFT", "BEDROOMS", "BATHROOMS", "AGE_YEARS", "GARAGE_SPACES",
             "LOT_SIZE_SQFT", "NEIGHBORHOOD", "CONDITION", "HAS_POOL"]
    ranked = sorted(zip(names, model.feature_importances_), key=lambda x: -x[1])
    return json.dumps({"feature_importance": {n: round(float(v), 3) for n, v in ranked},
                       "model": "GradientBoostingRegressor, R2 0.9897 on held-out data"})


class LookupInput(BaseModel):
    house_id: int = Field(ge=1, le=10000, description="House ID (1-10000)")


@tool(args_schema=LookupInput)
def lookup_house(house_id: int) -> str:
    """Return the full record (features and actual sale price) of ONE existing house by its ID.
    Use only when the user gives a specific house ID."""
    # Parameterised: the id is sent separately from the SQL text, so it can never change the query
    rows = db.query("SELECT * FROM dbo.house_prices WHERE id = ?", (house_id,))
    return json.dumps(rows[0], default=str) if rows else json.dumps({"error": f"No house with ID {house_id}"})


class StatsInput(BaseModel):
    sql: str = Field(description="A single T-SQL SELECT over dbo.house_prices")


@tool(args_schema=StatsInput)
def query_house_stats(sql: str) -> str:
    """Answer analytics questions about the 10,000 EXISTING house sales by writing ONE read-only T-SQL
    SELECT on dbo.house_prices (averages, counts, min/max, comparisons, group by).
    Columns: id, sqft, bedrooms, bathrooms, age_years, garage_spaces,
    neighborhood ('Downtown','Suburban','Rural','Waterfront','Urban'),
    condition ('Excellent','Good','Fair','Poor'), has_pool (bit 0/1), lot_size_sqft,
    month_sold, year_sold, price (USD).
    Use CAST(price AS FLOAT) inside AVG to avoid integer averages. Never write INSERT/UPDATE/DELETE/DROP.
    Do NOT use this to list individual houses - use list_houses for that."""
    try:
        safe_sql = validate_sql(sql)  # guard at the boundary: right before the database
    except GuardError as e:
        # Tell the LLM why, so it can rewrite the query or explain to the user
        return json.dumps({"error": f"Query rejected by guardrail: {e}"})
    rows = db.query(safe_sql)  # only the validated/rewritten SQL ever runs
    # Token backstop: aggregates return a few rows. If the LLM used this tool to list houses anyway,
    # pass at most 20 rows to the LLM (each row costs ~70 tokens) and point it to list_houses.
    if len(rows) > STATS_ROWS_TO_LLM:
        return json.dumps({"sql_executed": safe_sql, "rows": rows[:STATS_ROWS_TO_LLM], "truncated": True,
                           "rows_returned_by_db": len(rows),
                           "note": "Too many rows for an answer. Use list_houses to show lists to the user."},
                          default=str)
    return json.dumps({"sql_executed": safe_sql, "rows": rows}, default=str)


# ------------------------------------------------------------------------------------------ list_houses
# "Render, don't read": the matching rows go to the user's screen as a table (the tool ARTIFACT), and the
# LLM only sees a small summary (the tool CONTENT). Bulk data never enters the LLM's context window.
STATS_ROWS_TO_LLM = 20
LIST_COLUMNS = ["id", "neighborhood", "bedrooms", "bathrooms", "sqft", "age_years", "garage_spaces",
                "condition", "has_pool", "lot_size_sqft", "year_sold", "price"]
SortColumn = Literal["price", "sqft", "bedrooms", "bathrooms", "age_years", "lot_size_sqft", "year_sold", "id"]


class ListInput(BaseModel):
    """Structured filters instead of free SQL: the code builds a parameterised query from a fixed
    allow-list, so the LLM can't inject SQL and can't choose other tables or columns."""
    neighborhood: Neighborhood | None = None
    condition: Condition | None = None
    bedrooms: int | None = Field(default=None, ge=1, le=6, description="Exact number of bedrooms")
    min_bedrooms: int | None = Field(default=None, ge=1, le=6)
    min_bathrooms: int | None = Field(default=None, ge=1, le=4)
    has_pool: bool | None = None
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    min_sqft: int | None = Field(default=None, ge=0)
    max_sqft: int | None = Field(default=None, ge=0)
    max_age_years: int | None = Field(default=None, ge=0, le=50)
    year_sold: int | None = Field(default=None, ge=1900, le=2100)
    sort_by: SortColumn = "price"
    sort_desc: bool = Field(default=False, description="True = highest first")
    page: int = Field(default=1, ge=1, description="Page number, for 'show more' / 'next page'")
    page_size: int = Field(default=20, ge=1, le=50, description="Rows per page (max 50)")


def _where(f: ListInput) -> tuple[str, list]:
    """Build WHERE clause + params. Column names are fixed strings; every VALUE is a ? parameter."""
    conds, params = [], []
    for col, op, val in [("neighborhood", "=", f.neighborhood), ("condition", "=", f.condition),
                         ("bedrooms", "=", f.bedrooms), ("bedrooms", ">=", f.min_bedrooms),
                         ("bathrooms", ">=", f.min_bathrooms), ("price", ">=", f.min_price),
                         ("price", "<=", f.max_price), ("sqft", ">=", f.min_sqft), ("sqft", "<=", f.max_sqft),
                         ("age_years", "<=", f.max_age_years), ("year_sold", "=", f.year_sold)]:
        if val is not None:
            conds.append(f"{col} {op} ?")
            params.append(val)
    if f.has_pool is not None:
        conds.append("has_pool = ?")
        params.append(1 if f.has_pool else 0)
    return (" WHERE " + " AND ".join(conds)) if conds else "", params


@tool(args_schema=ListInput, response_format="content_and_artifact")
def list_houses(**kwargs):
    """List individual EXISTING houses matching filters (neighborhood, bedrooms, bathrooms, price range,
    size, condition, pool, age, year sold), sorted and paginated. Use for 'list/show/find houses with...'.
    The full table is displayed to the user automatically - you only receive a summary, so DO NOT try to
    repeat the rows. Tell the user how many matched and which page is shown. For 'next page' / 'show more',
    call again with the same filters and page + 1."""
    f = ListInput(**kwargs)
    where, params = _where(f)
    # 1. Total + price stats over ALL matches (one small row)
    stats = db.query("SELECT COUNT(*) AS total, MIN(price) AS min_price, MAX(price) AS max_price, "
                     f"AVG(CAST(price AS FLOAT)) AS avg_price FROM dbo.house_prices{where}", tuple(params))[0]
    total = stats["total"]
    # 2. Just this page. sort_by is a Literal (allow-listed), so putting it in the SQL text is safe.
    order = f"{f.sort_by} {'DESC' if f.sort_desc else 'ASC'}, id ASC"
    rows = db.query(f"SELECT {', '.join(LIST_COLUMNS)} FROM dbo.house_prices{where} ORDER BY {order} "
                    "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
                    tuple(params) + ((f.page - 1) * f.page_size, f.page_size))
    total_pages = max(1, -(-total // f.page_size))  # ceiling division
    filters = {k: v for k, v in f.model_dump().items()
               if v is not None and k not in ("sort_by", "sort_desc", "page", "page_size")}
    first = (f.page - 1) * f.page_size + 1 if rows else 0

    # CONTENT -> the LLM sees only this (~150 tokens, whatever the number of rows)
    summary = {
        "total_matching": total, "page": f.page, "total_pages": total_pages,
        "showing": f"{first}-{first + len(rows) - 1 if rows else 0} of {total}",
        "sorted_by": f"{f.sort_by} {'desc' if f.sort_desc else 'asc'}", "filters": filters,
        "price_over_all_matches": {"min": stats["min_price"], "max": stats["max_price"],
                                   "avg": round(stats["avg_price"], 0) if stats["avg_price"] else None},
        "displayed_to_user": "The rows are shown to the user as a table. Do not list them.",
    }
    # ARTIFACT -> the UI renders this; it is never sent to any LLM
    table = {"type": "table", "title": "Houses", "columns": LIST_COLUMNS,
             "rows": [[r[c] for c in LIST_COLUMNS] for r in rows],
             "total": total, "page": f.page, "page_size": f.page_size, "total_pages": total_pages,
             "showing": summary["showing"], "filters": filters, "sorted_by": summary["sorted_by"]}
    return json.dumps(summary, default=str), json.loads(json.dumps(table, default=str))


class CompareInput(HouseFeatures):  # same 9 features + an optional asking price
    listed_price: float | None = Field(default=None, description="Asking price in USD, if the user gave one")


@tool(args_schema=CompareInput)
def compare_to_market(listed_price: float | None = None, **kwargs) -> str:
    """Judge a house's price against the market: predicts its value AND compares with actual sales of
    similar houses (same neighborhood and bedrooms, sqft within 15%). Use for 'is this a good deal',
    'is it overpriced', or 'how does it compare' questions. Needs all 9 features; listed_price optional."""
    features = HouseFeatures(**kwargs)
    try:
        predicted, source = _predict(features)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Valuation service unavailable: {type(e).__name__}"})
    # Similar actual sales: same neighborhood and bedrooms, size within +/-15% (parameterised SQL)
    similar = db.query(
        "SELECT COUNT(*) AS n, AVG(CAST(price AS FLOAT)) AS avg_price, MIN(price) AS min_price, "
        "MAX(price) AS max_price FROM dbo.house_prices WHERE neighborhood = ? AND bedrooms = ? "
        "AND sqft BETWEEN ? AND ?",
        (features.neighborhood, features.bedrooms, int(features.sqft * 0.85), int(features.sqft * 1.15)))[0]
    result = {"predicted_price_usd": round(predicted, 2), "prediction_source": source,
              "similar_sales": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in similar.items()}}
    if listed_price:
        result["listed_vs_predicted_pct"] = round((listed_price - predicted) / predicted * 100, 1)
    return json.dumps(result, default=str)


# Everything the LLM may call. Not in this list = impossible for the agent to do.
TOOLS = [predict_price, explain_prediction, lookup_house, query_house_stats, compare_to_market, list_houses]
