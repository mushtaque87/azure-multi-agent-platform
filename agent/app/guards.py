"""Guardrails: input screening and the read-only SQL validator for text-to-SQL."""
import re

import sqlglot
from sqlglot import exp

from . import config

ALLOWED_TABLES = {"house_prices"}

# Cheap first-pass screen. Real protection = SQL validator + DB permissions + Prompt Shields (Phase 4).
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"(reveal|show|print|repeat).{0,30}(system prompt|instructions|api key|secret|password)",
    r"you are now",
    r"\bDAN\b",
]


class GuardError(ValueError):
    pass


def check_input(text: str) -> None:
    if not text or not text.strip():
        raise GuardError("Empty message.")
    if len(text) > config.MAX_INPUT_CHARS:
        raise GuardError(f"Message too long (max {config.MAX_INPUT_CHARS} characters).")
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            raise GuardError("This request looks like an attempt to override the assistant's instructions.")


def validate_sql(sql: str) -> str:
    """Return a safe single SELECT with a row cap, or raise GuardError."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="tsql") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"SQL could not be parsed: {e}") from e
    if len(statements) != 1:
        raise GuardError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise GuardError("Only SELECT queries are allowed.")
    # Reject anything that writes or escapes the table allow-list, anywhere in the tree (incl. subqueries)
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Into, exp.Command)
    if any(tree.find_all(*forbidden)):
        raise GuardError("Only read-only queries are allowed.")
    tables = {t.name.lower() for t in tree.find_all(exp.Table)}
    if not tables or not tables <= ALLOWED_TABLES:
        raise GuardError(f"Only these tables can be queried: {sorted(ALLOWED_TABLES)}.")
    if any(f.name.upper().startswith(("XP_", "SP_", "OPENROWSET", "OPENQUERY")) for f in tree.find_all(exp.Anonymous)):
        raise GuardError("System functions are not allowed.")
    # T-SQL AVG over INT/BIGINT columns does integer division (770,569 instead of 770,569.9).
    # Fix it deterministically instead of trusting the LLM to remember the prompt hint.
    def float_avg(node):
        if isinstance(node, exp.Avg) and not isinstance(node.this, exp.Cast):
            return exp.Avg(this=exp.Cast(this=node.this, to=exp.DataType.build("FLOAT")))
        return node
    tree = tree.transform(float_avg)
    # Cap rows: TOP n is added (or lowered) so a query can never dump the whole table
    limit = tree.args.get("limit")
    current = int(limit.expression.name) if limit and limit.expression.is_int else None
    if current is None or current > config.SQL_ROW_LIMIT:
        tree = tree.limit(config.SQL_ROW_LIMIT)
    return tree.sql(dialect="tsql")
