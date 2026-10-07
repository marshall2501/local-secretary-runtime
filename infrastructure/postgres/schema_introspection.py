"""Read-only PostgreSQL schema introspection for debug ER diagrams."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from application.schema_diagram import (
    SchemaColumn,
    SchemaForeignKey,
    SchemaSnapshot,
    SchemaTable,
)


_FK_ACTIONS = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}


def load_schema_snapshot(
    connection_factory: Callable,
    *,
    schema: str = "secretary",
) -> SchemaSnapshot:
    """Read schema metadata only; never reads business table rows."""
    with connection_factory() as db:
        with db.cursor() as cur:
            cur.execute("SELECT current_database()")
            database = str(cur.fetchone()[0])

            cur.execute(
                """SELECT table_name, table_type
                   FROM information_schema.tables
                   WHERE table_schema=%s
                   ORDER BY table_name""",
                (schema,),
            )
            relation_rows = list(cur.fetchall())

            cur.execute(
                """SELECT table_name, column_name, data_type, is_nullable,
                          column_default, ordinal_position
                   FROM information_schema.columns
                   WHERE table_schema=%s
                   ORDER BY table_name, ordinal_position""",
                (schema,),
            )
            column_rows = list(cur.fetchall())

            cur.execute(
                """SELECT tc.table_name, tc.constraint_name, tc.constraint_type,
                          kcu.column_name, kcu.ordinal_position
                   FROM information_schema.table_constraints AS tc
                   JOIN information_schema.key_column_usage AS kcu
                     ON kcu.constraint_schema=tc.constraint_schema
                    AND kcu.constraint_name=tc.constraint_name
                    AND kcu.table_schema=tc.table_schema
                    AND kcu.table_name=tc.table_name
                   WHERE tc.table_schema=%s
                     AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
                   ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position""",
                (schema,),
            )
            constraint_rows = list(cur.fetchall())

            cur.execute(
                """SELECT con.conname,
                          src.relname AS source_table,
                          src_att.attname AS source_column,
                          tgt.relname AS target_table,
                          tgt_att.attname AS target_column,
                          src_key.ord,
                          con.confupdtype,
                          con.confdeltype
                   FROM pg_constraint AS con
                   JOIN pg_class AS src ON src.oid=con.conrelid
                   JOIN pg_namespace AS ns ON ns.oid=src.relnamespace
                   JOIN pg_class AS tgt ON tgt.oid=con.confrelid
                   JOIN LATERAL unnest(con.conkey) WITH ORDINALITY
                        AS src_key(attnum, ord) ON TRUE
                   JOIN LATERAL unnest(con.confkey) WITH ORDINALITY
                        AS tgt_key(attnum, ord) ON tgt_key.ord=src_key.ord
                   JOIN pg_attribute AS src_att
                     ON src_att.attrelid=src.oid AND src_att.attnum=src_key.attnum
                   JOIN pg_attribute AS tgt_att
                     ON tgt_att.attrelid=tgt.oid AND tgt_att.attnum=tgt_key.attnum
                   WHERE con.contype='f' AND ns.nspname=%s
                   ORDER BY con.conname, src_key.ord""",
                (schema,),
            )
            fk_rows = list(cur.fetchall())

            cur.execute(
                """SELECT tablename, indexname
                   FROM pg_indexes
                   WHERE schemaname=%s
                   ORDER BY tablename, indexname""",
                (schema,),
            )
            index_rows = list(cur.fetchall())

    columns_by_table: dict[str, list[SchemaColumn]] = defaultdict(list)
    pk_by_table: dict[str, list[str]] = defaultdict(list)
    unique_by_constraint: dict[tuple[str, str], list[str]] = defaultdict(list)
    index_by_table: dict[str, list[str]] = defaultdict(list)
    fk_columns: set[tuple[str, str]] = set()

    fk_group: dict[str, dict] = {}
    for (
        constraint_name,
        source_table,
        source_column,
        target_table,
        target_column,
        _ordinal,
        update_code,
        delete_code,
    ) in fk_rows:
        item = fk_group.setdefault(
            str(constraint_name),
            {
                "source_table": str(source_table),
                "source_columns": [],
                "target_table": str(target_table),
                "target_columns": [],
                "update_rule": _FK_ACTIONS.get(str(update_code), str(update_code)),
                "delete_rule": _FK_ACTIONS.get(str(delete_code), str(delete_code)),
            },
        )
        item["source_columns"].append(str(source_column))
        item["target_columns"].append(str(target_column))
        fk_columns.add((str(source_table), str(source_column)))

    for table_name, constraint_name, constraint_type, column_name, _ordinal in constraint_rows:
        table = str(table_name)
        column = str(column_name)
        if constraint_type == "PRIMARY KEY":
            pk_by_table[table].append(column)
        else:
            unique_by_constraint[(table, str(constraint_name))].append(column)

    for table_name, index_name in index_rows:
        index_by_table[str(table_name)].append(str(index_name))

    pk_columns = {
        (table, column)
        for table, columns in pk_by_table.items()
        for column in columns
    }
    for table_name, column_name, data_type, is_nullable, column_default, _ordinal in column_rows:
        key = (str(table_name), str(column_name))
        columns_by_table[str(table_name)].append(
            SchemaColumn(
                name=str(column_name),
                data_type=str(data_type),
                nullable=str(is_nullable).upper() == "YES",
                default_present=column_default is not None,
                primary_key=key in pk_columns,
                foreign_key=key in fk_columns,
            )
        )

    tables: list[SchemaTable] = []
    views: list[str] = []
    for table_name, table_type in relation_rows:
        name = str(table_name)
        kind = str(table_type)
        if kind == "VIEW":
            views.append(name)
        unique_constraints = tuple(
            tuple(columns)
            for (table, _constraint), columns in sorted(unique_by_constraint.items())
            if table == name
        )
        tables.append(
            SchemaTable(
                name=name,
                kind=kind,
                columns=tuple(columns_by_table.get(name, ())),
                primary_key=tuple(pk_by_table.get(name, ())),
                unique_constraints=unique_constraints,
                indexes=tuple(index_by_table.get(name, ())),
            )
        )

    foreign_keys = tuple(
        SchemaForeignKey(
            constraint_name=name,
            source_table=item["source_table"],
            source_columns=tuple(item["source_columns"]),
            target_table=item["target_table"],
            target_columns=tuple(item["target_columns"]),
            update_rule=item["update_rule"],
            delete_rule=item["delete_rule"],
        )
        for name, item in sorted(fk_group.items())
    )

    return SchemaSnapshot(
        database=database,
        schema=schema,
        tables=tuple(tables),
        foreign_keys=foreign_keys,
        views=tuple(sorted(views)),
    )
