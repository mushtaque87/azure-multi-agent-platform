"""Guardrails: input screening and the read-only SQL validator for text-to-SQL.

Pure Python rules with no LangGraph or LLM inside, so they are unit-testable (tests/test_guards.py)
and reusable by other agents. graph.py decides WHEN check_input runs; tools.py calls validate_sql.
"""
import re  # regular expressions for the injection phrase screen

import sqlglot          # SQL parser: turns SQL text into a syntax tree we can inspect and rewrite
from sqlglot import exp  # node types of that tree (Select, Delete, Table, Avg, ...)

from . import config

# Agent-specific: the only table the LLM's SQL may read. Everything else (incl. sys.*) is rejected.
ALLOWED_TABLES = {"house_prices"}

# Cheap first-pass screen of known attack phrases. Regex only catches wording it knows, so this is
# NOT the real protection - that is the SQL validator + DB permissions + Prompt Shields (Phase 4).
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",                         # classic override
    r"(reveal|show|print|repeat).{0,30}(system prompt|instructions|api key|secret|password)",  # extraction
    r"you are now",                                                                     # role hijack
    r"\bDAN\b",                                                                         # "Do Anything Now" jailbreak
]


class GuardError(ValueError):
    """Raised when a guard rejects something. Callers catch exactly this and turn it into a polite
    refusal; any other exception is a real bug and is not silently swallowed."""


def check_input(text: str) -> None:
    """Generic input guard: returns None if OK, raises GuardError if not."""
    if not text or not text.strip():                  # nothing to answer
        raise GuardError("Empty message.")
    if len(text) > config.MAX_INPUT_CHARS:            # long inputs cost tokens and hide injections
        raise GuardError(f"Message too long (max {config.MAX_INPUT_CHARS} characters).")
    for pattern in INJECTION_PATTERNS:                # case-insensitive phrase match
        if re.search(pattern, text, re.IGNORECASE):
            raise GuardError("This request looks like an attempt to override the assistant's instructions.")


def validate_sql(sql: str) -> str:
    """Return a safe single SELECT with a row cap, or raise GuardError.
    The LLM wrote this SQL, so it is untrusted input. We parse it (understand its structure)
    instead of searching the text, which would miss 'dElEtE' or wrongly block a column 'deleted_at'."""
    try:
        # Parse with the T-SQL (SQL Server / Azure SQL) dialect; one tree per statement
        statements = [s for s in sqlglot.parse(sql, read="tsql") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"SQL could not be parsed: {e}") from e  # fail closed: unreadable = rejected
    if len(statements) != 1:                          # blocks stacked queries: SELECT 1; DROP TABLE x
        raise GuardError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Select):              # top level must be a read: no DELETE/UPDATE/EXEC
        raise GuardError("Only SELECT queries are allowed.")
    # Search the WHOLE tree, including subqueries, for anything that writes or runs commands.
    # exp.Into catches SELECT ... INTO new_table (which creates a table).
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Into, exp.Command)
    if any(tree.find_all(*forbidden)):
        raise GuardError("Only read-only queries are allowed.")
    # Every table referenced anywhere must be on the allow-list (blocks sys.sql_logins etc.)
    tables = {t.name.lower() for t in tree.find_all(exp.Table)}
    if not tables or not tables <= ALLOWED_TABLES:    # "<=" on sets = "is a subset of"
        raise GuardError(f"Only these tables can be queried: {sorted(ALLOWED_TABLES)}.")
    # Unknown functions are parsed as exp.Anonymous; block system procs and external data readers
    if any(f.name.upper().startswith(("XP_", "SP_", "OPENROWSET", "OPENQUERY")) for f in tree.find_all(exp.Anonymous)):
        raise GuardError("System functions are not allowed.")

    # Correctness guard (not security): T-SQL AVG over INT/BIGINT columns does integer division
    # (770,569 instead of 770,569.9). Fix it deterministically instead of trusting the LLM to
    # remember the prompt hint.
    def float_avg(node):
        if isinstance(node, exp.Avg) and not isinstance(node.this, exp.Cast):
            return exp.Avg(this=exp.Cast(this=node.this, to=exp.DataType.build("FLOAT")))
        return node
    tree = tree.transform(float_avg)  # apply the rewrite to every node in the tree

    # Cap rows: TOP n is added (or lowered) so a query can never dump the whole table
    limit = tree.args.get("limit")  # existing TOP/LIMIT, if any
    current = int(limit.expression.name) if limit and limit.expression.is_int else None
    if current is None or current > config.SQL_ROW_LIMIT:
        tree = tree.limit(config.SQL_ROW_LIMIT)
    return tree.sql(dialect="tsql")  # turn the (possibly rewritten) tree back into T-SQL text; this is what runs
