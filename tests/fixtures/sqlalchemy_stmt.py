"""SQLAlchemy statement inspection helpers for FakeSession unit tests."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.selectable import Select


def is_select_stmt(stmt: object) -> bool:
    """Return True for ORM/Core SELECT (not INSERT CTE persist)."""
    return isinstance(stmt, Select)


def stmt_targets_table(stmt: object, table_name: str) -> bool:
    """Return True when a SELECT targets the given table name."""
    if not is_select_stmt(stmt):
        return False
    for entity in stmt._raw_columns:
        table = getattr(getattr(entity, "entity_namespace", None), "__table__", None)
        if table is not None and table.name == table_name:
            return True
    return False


def compiled_sql(stmt: object) -> str:
    """Lowercase PostgreSQL SQL for persist-statement assertions."""
    return str(stmt.compile(dialect=postgresql.dialect())).lower()


def compiled_param_values(stmt: object) -> list[str]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return [str(value) for value in compiled.params.values()]
