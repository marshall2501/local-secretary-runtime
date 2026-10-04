"""Shared helpers for trusted local PostgreSQL data promotion.

Only checked-in table/query definitions call these helpers. Values stay in-memory
and callers print counts only.
"""
from __future__ import annotations

from collections.abc import Sequence

from psycopg import sql
from psycopg.types.json import Json, Jsonb


def one(cur, statement: str, args=()):
    cur.execute(statement, args)
    row = cur.fetchone()
    if row is None or len(row) != 1:
        raise RuntimeError("unexpected scalar result")
    return row[0]


def relation_columns(cur, table: str) -> tuple[tuple[str, ...], dict[str, str]]:
    cur.execute(
        """SELECT column_name, udt_name
           FROM information_schema.columns
           WHERE table_schema='secretary' AND table_name=%s
           ORDER BY ordinal_position""",
        (table,),
    )
    rows = cur.fetchall()
    if not rows:
        raise RuntimeError(f"missing transfer relation: {table}")
    columns = tuple(row[0] for row in rows)
    types = {row[0]: row[1] for row in rows}
    return columns, types


def _adapt(value, udt_name: str):
    if value is None:
        return None
    if udt_name == "jsonb":
        return Jsonb(value)
    if udt_name == "json":
        return Json(value)
    return value


def _select_existing(target_cur, table: str, columns: Sequence[str],
                     key_columns: Sequence[str], row: Sequence):
    column_index = {name: index for index, name in enumerate(columns)}
    predicates = []
    values = []
    for key in key_columns:
        predicates.append(sql.SQL("{} = %s").format(sql.Identifier(key)))
        values.append(row[column_index[key]])
    query = sql.SQL("SELECT {} FROM secretary.{} WHERE {}").format(
        sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.Identifier(table),
        sql.SQL(" AND ").join(predicates),
    )
    target_cur.execute(query, tuple(values))
    return target_cur.fetchone()


def copy_query_rows(
    source_cur,
    target_cur,
    *,
    table: str,
    key_columns: Sequence[str],
    source_query_template: str,
    source_args=(),
) -> dict[str, int]:
    source_columns, _ = relation_columns(source_cur, table)
    target_columns, target_types = relation_columns(target_cur, table)
    if source_columns != target_columns:
        raise RuntimeError(f"source/target column mismatch: {table}")
    for key in key_columns:
        if key not in target_columns:
            raise RuntimeError(f"missing transfer key {table}.{key}")

    column_sql = sql.SQL(",").join(map(sql.Identifier, target_columns))
    source_query = sql.SQL(source_query_template).format(columns=column_sql)
    source_cur.execute(source_query, source_args)
    rows = source_cur.fetchall()

    placeholders = sql.SQL(",").join(sql.Placeholder() for _ in target_columns)
    insert_query = sql.SQL("INSERT INTO secretary.{} ({}) VALUES ({})").format(
        sql.Identifier(table),
        column_sql,
        placeholders,
    )

    inserted = 0
    skipped = 0
    for row in rows:
        existing = _select_existing(
            target_cur, table, target_columns, key_columns, row
        )
        if existing is not None:
            if tuple(existing) != tuple(row):
                raise RuntimeError(f"data collision in {table}")
            skipped += 1
            continue
        adapted = tuple(
            _adapt(value, target_types[column])
            for column, value in zip(target_columns, row, strict=True)
        )
        target_cur.execute(insert_query, adapted)
        inserted += 1

    return {
        "source": len(rows),
        "inserted": inserted,
        "skipped_equal": skipped,
    }


def copy_all_rows(source_cur, target_cur, *, table: str,
                  key_columns: Sequence[str], order_by: str) -> dict[str, int]:
    if not order_by.replace("_", "").replace(",", "").replace(" ", "").isalnum():
        raise RuntimeError("unsafe transfer order")
    return copy_query_rows(
        source_cur,
        target_cur,
        table=table,
        key_columns=key_columns,
        source_query_template=(
            f"SELECT {{columns}} FROM secretary.{table} ORDER BY {order_by}"
        ),
    )
