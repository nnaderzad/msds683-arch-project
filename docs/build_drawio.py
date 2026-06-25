"""Generate a draw.io (.drawio) ERD from the Mermaid blocks in data-model.md.

Single source of truth is ``docs/data-model.md``: this script parses its
``erDiagram`` fenced blocks (entities + attributes + relationships) and emits a
``.drawio`` XML file with one page per erDiagram. The result imports into
Lucidchart via *File -> Import* (Direct file import supports Draw.io .drawio/.xml)
as editable, draggable shapes.

Deterministic and re-runnable; no network, no LLM:

    python docs/build_drawio.py                 # md -> docs/data-model.drawio
    python docs/build_drawio.py --input X --output Y
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.dom import minidom
from xml.sax.saxutils import escape

# One nice name per erDiagram block, in document order (silver first, gold second).
PAGE_NAMES = ["Silver - source constellation", "Gold - fact_event_demand star"]
# Short slug per layer, used for the standalone per-layer .mmd filenames.
SLUGS = ["silver", "gold"]

# Colours by table role (draw.io standard palette).
ROLE_STYLE = {
    "dim": ("#dae8fc", "#6c8ebf"),
    "fact": ("#ffe6cc", "#d79b00"),
    "bridge": ("#d5e8d4", "#82b366"),
    "other": ("#f5f5f5", "#666666"),
}

HEADER_H = 30
ROW_H = 24
BOX_W = 270
GAP = 40
N_COLS = 3


@dataclass
class Column:
    name: str
    type: str
    keys: list[str]
    comment: str


@dataclass
class Entity:
    name: str
    columns: list[Column] = field(default_factory=list)


@dataclass
class Rel:
    source: str
    target: str
    start_arrow: str
    end_arrow: str
    label: str
    left_mult: str = "1"
    right_mult: str = "0..*"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def extract_er_blocks(md_text: str) -> list[str]:
    """Return the inner text of every ```mermaid``` block that is an erDiagram."""
    blocks = re.findall(r"```mermaid\n(.*?)```", md_text, re.DOTALL)
    return [b for b in blocks if b.lstrip().startswith("erDiagram")]


def _arrow(half: str) -> str:
    """Map one half of a Mermaid cardinality token to a draw.io ER arrow name."""
    many = "{" in half or "}" in half
    zero = "o" in half
    if many:
        return "ERzeroToMany" if zero else "ERoneToMany"
    return "ERzeroToOne" if zero else "ERmandOne"


def _mult(half: str) -> str:
    """Map one half of a Mermaid cardinality token to a UML multiplicity string."""
    many = "{" in half or "}" in half
    zero = "o" in half
    if many:
        return "0..*" if zero else "1..*"
    return "0..1" if zero else "1"


def _parse_attr(line: str) -> Column:
    comment = ""
    mq = re.search(r'"([^"]*)"', line)
    if mq:
        comment = mq.group(1)
        line = line[: mq.start()] + line[mq.end():]
    toks = line.split()
    ctype, name = toks[0], toks[1]
    keys = [t.strip(",").upper() for t in toks[2:] if t.strip(",").upper() in ("PK", "FK", "UK")]
    return Column(name=name, type=ctype, keys=keys, comment=comment)


def parse_er_block(block: str) -> tuple[dict[str, Entity], list[Rel]]:
    entities: dict[str, Entity] = {}
    rels: list[Rel] = []
    current: str | None = None

    for raw in block.splitlines():
        line = raw.strip()
        if not line or line == "erDiagram":
            continue

        if current is not None:  # inside a multi-line entity body
            if line == "}":
                current = None
            else:
                entities[current].columns.append(_parse_attr(line))
            continue

        m_inline = re.match(r"^([A-Za-z_]\w*)\s*\{(.*)\}\s*$", line)
        if m_inline:
            name, inner = m_inline.group(1), m_inline.group(2).strip()
            entities.setdefault(name, Entity(name))
            if inner:
                entities[name].columns.append(_parse_attr(inner))
            continue

        m_start = re.match(r"^([A-Za-z_]\w*)\s*\{$", line)
        if m_start:
            name = m_start.group(1)
            entities.setdefault(name, Entity(name))
            current = name
            continue

        m_rel = re.match(r"^([A-Za-z_]\w*)\s+(\S+)\s+([A-Za-z_]\w*)\s*:\s*(.*)$", line)
        if m_rel:
            src, card, tgt, label = m_rel.groups()
            left, right = card.split("--") if "--" in card else (card, card)
            entities.setdefault(src, Entity(src))
            entities.setdefault(tgt, Entity(tgt))
            rels.append(
                Rel(
                    src, tgt, _arrow(left), _arrow(right), label.strip(),
                    left_mult=_mult(left), right_mult=_mult(right),
                )
            )

    return entities, rels


# --------------------------------------------------------------------------- #
# draw.io XML emission
# --------------------------------------------------------------------------- #
def _role(name: str) -> str:
    for prefix in ("dim", "fact", "bridge"):
        if name.startswith(prefix + "_"):
            return prefix
    return "other"


def _layout(entities: dict[str, Entity]) -> dict[str, tuple[int, int, int]]:
    """Shelf-pack boxes into N_COLS columns; return name -> (x, y, height)."""
    col_y = [GAP] * N_COLS
    placed: dict[str, tuple[int, int, int]] = {}
    for ent in entities.values():
        col = min(range(N_COLS), key=lambda c: col_y[c])
        height = HEADER_H + max(1, len(ent.columns)) * ROW_H
        x = GAP + col * (BOX_W + GAP)
        placed[ent.name] = (x, col_y[col], height)
        col_y[col] += height + GAP
    return placed


def _col_text(c: Column) -> str:
    prefix = ("/".join(c.keys) + "  ") if c.keys else ""
    core = f"{c.name} : {c.type}"
    tail = f"   - {c.comment}" if c.comment else ""
    return prefix + core + tail


def build_page_xml(entities: dict[str, Entity], rels: list[Rel], page_id: str) -> list[str]:
    placed = _layout(entities)
    cells: list[str] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']

    for ent in entities.values():
        fill, stroke = ROLE_STYLE[_role(ent.name)]
        tid = f"{page_id}_{ent.name}"
        x, y, h = placed[ent.name]
        cstyle = (
            f"swimlane;startSize={HEADER_H};html=1;childLayout=stackLayout;horizontal=1;"
            "resizeParent=1;resizeParentMax=0;horizontalStack=0;collapsible=0;"
            f"fontStyle=1;fontSize=13;fillColor={fill};strokeColor={stroke};"
        )
        cells.append(
            f'<mxCell id="{tid}" value="{escape(ent.name)}" style="{cstyle}" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{BOX_W}" height="{h}" as="geometry"/></mxCell>'
        )
        rstyle = (
            "shape=partialRectangle;html=1;whiteSpace=wrap;align=left;verticalAlign=middle;"
            "spacingLeft=8;top=1;left=0;bottom=0;right=0;fillColor=none;overflow=hidden;"
            f"strokeColor={stroke};fontSize=12;"
        )
        for i, col in enumerate(ent.columns):
            rid = f"{tid}_r{i}"
            cells.append(
                f'<mxCell id="{rid}" value="{escape(_col_text(col))}" style="{rstyle}" '
                f'vertex="1" parent="{tid}">'
                f'<mxGeometry y="{HEADER_H + i * ROW_H}" width="{BOX_W}" height="{ROW_H}" '
                f'as="geometry"/></mxCell>'
            )

    for i, r in enumerate(rels):
        estyle = (
            "edgeStyle=entityRelationEdgeStyle;rounded=0;html=1;fontSize=11;"
            f"startArrow={r.start_arrow};startFill=0;endArrow={r.end_arrow};endFill=0;"
        )
        cells.append(
            f'<mxCell id="{page_id}_e{i}" value="{escape(r.label)}" style="{estyle}" '
            f'edge="1" parent="1" source="{page_id}_{r.source}" target="{page_id}_{r.target}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
    return cells


def build_drawio(pages: list[tuple[str, dict[str, Entity], list[Rel]]]) -> str:
    parts = ['<mxfile host="app.diagrams.net">']
    for idx, (name, entities, rels) in enumerate(pages):
        page_id = f"p{idx}"
        cells = build_page_xml(entities, rels, page_id)
        parts.append(f'<diagram id="{page_id}" name="{escape(name)}">')
        parts.append(
            '<mxGraphModel dx="1000" dy="800" grid="1" gridSize="10" guides="1" '
            'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            'pageWidth="1700" pageHeight="2200" math="0" shadow="0"><root>'
        )
        parts.extend(cells)
        parts.append("</root></mxGraphModel></diagram>")
    parts.append("</mxfile>")
    return minidom.parseString("".join(parts)).toprettyxml(indent="  ")


# --------------------------------------------------------------------------- #
# UML class-diagram emission (Mermaid classDiagram)
# --------------------------------------------------------------------------- #
def to_classdiagram(entities: dict[str, Entity], rels: list[Rel]) -> str:
    """Render one parsed layer as a Mermaid UML classDiagram block."""
    lines = ["classDiagram"]
    for ent in entities.values():
        lines.append(f"    class {ent.name} {{")
        for c in ent.columns:
            key = f" [{','.join(c.keys)}]" if c.keys else ""
            lines.append(f"        +{c.name} : {c.type}{key}")
        lines.append("    }")
    for r in rels:
        label = f" : {r.label}" if r.label else ""
        lines.append(f'    {r.source} "{r.left_mult}" -- "{r.right_mult}" {r.target}{label}')
    return "\n".join(lines)


def build_uml_markdown(pages: list[tuple[str, dict[str, Entity], list[Rel]]]) -> str:
    out = ["# Data model — UML class diagrams", "", "*Generated by `build_drawio.py` "
           "from the ER blocks in `data-model.md`. Do not edit by hand.*", ""]
    for name, entities, rels in pages:
        out += [f"## {name}", "", "```mermaid", to_classdiagram(entities, rels), "```", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    ap.add_argument("--input", type=Path, default=here / "data-model.md")
    ap.add_argument("--output", type=Path, default=here / "data-model.drawio")
    ap.add_argument("--uml", type=Path, default=here / "data-model-uml.md",
                    help="also write a Mermaid UML class-diagram markdown here")
    ap.add_argument("--mmd-dir", type=Path, default=here / "mermaid",
                    help="write standalone per-layer .mmd files here (ER + UML)")
    args = ap.parse_args()

    args.mmd_dir.mkdir(parents=True, exist_ok=True)
    blocks = extract_er_blocks(args.input.read_text())
    pages = []
    for idx, block in enumerate(blocks):
        entities, rels = parse_er_block(block)
        page_name = PAGE_NAMES[idx] if idx < len(PAGE_NAMES) else f"Page {idx + 1}"
        pages.append((page_name, entities, rels))
        print(f"  {page_name}: {len(entities)} entities, {len(rels)} relationships")

        slug = SLUGS[idx] if idx < len(SLUGS) else f"page{idx + 1}"
        (args.mmd_dir / f"{slug}.er.mmd").write_text(block.strip() + "\n")
        (args.mmd_dir / f"{slug}.uml.mmd").write_text(to_classdiagram(entities, rels) + "\n")
        print(f"  wrote {slug}.er.mmd and {slug}.uml.mmd")

    args.output.write_text(build_drawio(pages))
    print(f"Wrote {args.output} ({len(pages)} page(s))")
    args.uml.write_text(build_uml_markdown(pages))
    print(f"Wrote {args.uml} (UML class diagrams)")


if __name__ == "__main__":
    main()
