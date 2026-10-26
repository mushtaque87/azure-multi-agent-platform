"""The agent's tools. Each has a Pydantic schema (validated BEFORE any I/O) and a precise description,
because the LLM chooses tools by reading their names, descriptions and argument schemas."""
import json
import os
from functools import lru_cache
from typing import Literal

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from . import config, db
from .guards import GuardError, validate_sql

FEATURES = ["SQFT", "BEDROOMS", "BATHROOMS", "AGE_YEARS", "GARAGE_SPACES",
            "NEIGHBORHOOD", "CONDITION", "HAS_POOL", "LOT_SIZE_SQFT"]
Neighborhood = Literal["Downtown", "Suburban", "Rural", "Waterfront", "Urban"]
Condition = Literal["Excellent", "Good", "Fair", "Poor"]


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

    def as_row(self):
        return [self.sqft, self.bedrooms, self.bathrooms, self.age_years, self.garage_spaces,
                self.neighborhood, self.condition, self.has_pool, self.lot_size_sqft]


@lru_cache(maxsize=1)
def _local_model():
    import joblib
    return (joblib.load(os.path.join(config.LOCAL_MODEL_DIR, "house_price_model.joblib")),
            joblib.load(os.path.join(config.LOCAL_MODEL_DIR, "ordinal_encoder.joblib")))


def _predict(features: HouseFeatures) -> tuple[float, str]:
    """Azure ML endpoint if configured, else the same model loaded locally. Returns (price, source)."""
    if config.ML_ENDPOINT_URL:
        body = {"input_data": {"columns": FEATURES, "data": [features.as_row()]}}
        r = httpx.post(config.ML_ENDPOINT_URL, json=body, timeout=30,
                       headers={"Authorization": f"Bearer {config.ML_ENDPOINT_KEY}"})
        r.raise_for_status()
        return float(r.json()["predictions"][0]), "azure-ml-endpoint"
    import pandas as pd
    model, encoder = _local_model()
    row = dict(zip(FEATURES, features.as_row()))
    row["NEIGHBORHOOD"], row["CONDITION"] = encoder.transform([[row["NEIGHBORHOOD"], row["CONDITION"]]])[0]
    row["HAS_POOL"] = int(row["HAS_POOL"])
    order = ["SQFT", "BEDROOMS", "BATHROOMS", "AGE_YEARS", "GARAGE_SPACES",
             "LOT_SIZE_SQFT", "NEIGHBORHOOD", "CONDITION", "HAS_POOL"]
    return float(model.predict(pd.DataFrame([row])[order].astype(float))[0]), "local-model"


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
    rows = db.query("SELECT * FROM dbo.house_prices WHERE id = ?", (house_id,))  # parameterised: no injection
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
    Use CAST(price AS FLOAT) inside AVG to avoid integer averages. Never write INSERT/UPDATE/DELETE/DROP."""
    try:
        safe_sql = validate_sql(sql)
    except GuardError as e:
        return json.dumps({"error": f"Query rejected by guardrail: {e}"})
    rows = db.query(safe_sql)
    return json.dumps({"sql_executed": safe_sql, "rows": rows}, default=str)


class CompareInput(HouseFeatures):
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


TOOLS = [predict_price, explain_prediction, lookup_house, query_house_stats, compare_to_market]
