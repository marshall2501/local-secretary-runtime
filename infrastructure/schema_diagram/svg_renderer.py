"""Deterministic offline SVG diagram of PostgreSQL schema metadata.

A rendering adapter for the existing SchemaSnapshot Application contract.
No JS/CDN dependency, SQL row content, external binary or NiceGUI coupling.
"""
from __future__ import annotations

from html import escape
import time

from application.schema_diagram import (
    SchemaSnapshot, ProviderStatus, SchemaDiagramResult,
)


def render_svg(snapshot: SchemaSnapshot, *, keys_only: bool = True) -> str:
    """Draw linked table cards; input contains schema metadata, not table rows."""
    tables = sorted(
        (table for table in snapshot.tables if table.kind == "BASE TABLE"),
        key=lambda table: table.name,
    )
    number_of_columns = min(4, max(1, len(tables)))
    card_width, gap, margin = 332, 42, 36
    row_items = [
        tables[i:i + number_of_columns]
        for i in range(0, len(tables), number_of_columns)
    ]
    positions: dict[str, tuple[int, int, int]] = {}
    rows: list[tuple[list, int, int]] = []
    y = 116
    for group in row_items:
        max_lines = max(
            len([c for c in table.columns if not keys_only or c.primary_key or c.foreign_key])
            for table in group
        )
        card_height = max(120, 62 + 21 * max_lines)
        rows.append((group, y, card_height))
        for i, table in enumerate(group):
            positions[table.name] = (margin + i * (card_width + gap), y, card_height)
        y += card_height + 54

    width = 2 * margin + number_of_columns * card_width + (number_of_columns - 1) * gap
    height = max(235, y + 12)
    name = escape(snapshot.database, quote=True)
    schema = escape(snapshot.schema, quote=True)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="ER diagram for {name}">',
        '<defs><marker id="fk-arrow" markerWidth="9" markerHeight="9" '
        'refX="7" refY="4" orient="auto" markerUnits="strokeWidth">'
        '<path d="M1 1 L7 4 L1 7" fill="none" stroke="#64748b" stroke-width="1.5"/>'
        '</marker></defs>',
        f'<rect width="{width}" height="{height}" rx="16" fill="#f8fafc"/>',
        '<text x="36" y="43" font-family="system-ui,sans-serif" '
        'font-size="24" font-weight="700" fill="#0f172a">',
        f'{name} / {schema}</text>',
        f'<text x="36" y="73" font-family="system-ui,sans-serif" '
        'font-size="14" fill="#64748b">{len(tables)} tables '
        f'/ {len(snapshot.foreign_keys)} foreign keys</text>',
    ]
    # Draw connections underneath card content. A different column anchor
    # produces a side-to-side curve; same-column edges loop at the right.
    for fk in sorted(snapshot.foreign_keys,
                     key=lambda f: (f.source_table, f.target_table, f.constraint_name)):
        if fk.source_table not in positions or fk.target_table not in positions:
            continue
        sx, sy, sh = positions[fk.source_table]
        tx, ty, th = positions[fk.target_table]
        if sx < tx:
            x1, x2 = sx + card_width, tx
        elif sx > tx:
            x1, x2 = sx, tx + card_width
        else:
            x1 = x2 = sx + card_width
        y1, y2 = sy + min(51, sh / 2), ty + min(51, th / 2)
        bend = max(36, abs(x2 - x1) * .45)
        direction = 1 if sx <= tx else -1
        if sx == tx:
            x2 += 18
        cx1, cx2 = x1 + bend * direction, x2 - bend * direction
        out.append(
            f'<path d="M{x1:.0f} {y1:.0f} C{cx1:.0f} {y1:.0f}, '
            f'{cx2:.0f} {y2:.0f}, {x2:.0f} {y2:.0f}" '
            f'fill="none" stroke="#94a3b8" stroke-width="2" '
            f'stroke-opacity=".85" marker-end="url(#fk-arrow)">'
            f'<title>{escape(fk.source_table + "." + ",".join(fk.source_columns) + " → " + fk.target_table + "." + ",".join(fk.target_columns))}</title>'
            '</path>'
        )
    for group, ty, card_height in rows:
        for table in group:
            x, top, _ = positions[table.name]
            cols = [
                col for col in table.columns
                if not keys_only or col.primary_key or col.foreign_key
            ]
            table_name = escape(table.name)
            out.extend([
                f'<g><rect x="{x}" y="{top}" width="{card_width}" height="{card_height}" '
                'rx="9" fill="white" stroke="#cbd5e1" stroke-width="1.5"/>',
                f'<path d="M{x + 9} {top} H{x + card_width - 9} '
                f'Q{x + card_width} {top} {x + card_width} {top + 9} '
                f'V{top + 39} H{x} V{top + 9} Q{x} {top} {x + 9} {top}" '
                'fill="#dbeafe"/>',
                f'<text x="{x + 14}" y="{top + 27}" font-family="system-ui,sans-serif" '
                f'font-size="17" font-weight="700" fill="#1e3a8a">{table_name}</text>',
                f'<title>{table_name}</title>',
            ])
            for k, col in enumerate(cols):
                label = "PK" if col.primary_key else "FK" if col.foreign_key else ""
                color = "#2563eb" if col.primary_key else "#0f766e" if col.foreign_key else "#334155"
                out.append(
                    f'<text x="{x + 13}" y="{top + 57 + k * 21}" '
                    f'font-family="ui-monospace,monospace" font-size="12.5" '
                    f'fill="{color}">{escape(col.name)}'
                    f'<tspan fill="#64748b"> : {escape(col.data_type)}</tspan>'
                    f' <tspan font-weight="700">{label}</tspan></text>'
                )
            if not cols:
                out.append(
                    f'<text x="{x + 13}" y="{top + 59}" '
                    'font-size="13" fill="#64748b">表示するキーがありません</text>'
                )
            out.append('</g>')
    out.append('</svg>')
    return "\n".join(out)


class NativeSvgProvider:
    """Offline SVG implementation of the existing ER Diagram provider port."""

    key = "native_svg"
    display_name = "Native SVG"

    def __init__(self, snapshot_loader):
        self._snapshot_loader = snapshot_loader

    def status(self, *, enabled: bool) -> ProviderStatus:
        return ProviderStatus(
            key=self.key, display_name=self.display_name,
            enabled=enabled, available=True, version="built-in",
            dependency_status="PostgreSQL schema metadata only",
        )

    def generate(self, *, options: dict[str, object] | None = None
                 ) -> SchemaDiagramResult:
        started = time.perf_counter()
        snapshot = self._snapshot_loader()
        requested = options or {}
        diagram = render_svg(snapshot, keys_only=bool(requested.get("keys_only", True)))
        return SchemaDiagramResult(
            status="ok", provider=self.key, database=snapshot.database,
            schema=snapshot.schema, output_format="svg", content=diagram,
            table_count=snapshot.table_count,
            relation_count=snapshot.relation_count,
            duration_ms=int((time.perf_counter() - started) * 1000),
            generated_at=snapshot.generated_at,
        )
