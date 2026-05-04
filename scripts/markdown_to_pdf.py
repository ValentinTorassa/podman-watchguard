#!/usr/bin/env python3
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def clean_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return html.escape(text, quote=False).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")


def strip_frontmatter(lines: list[str]) -> list[str]:
    if not lines or lines[0].strip() != "---":
        return lines
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[index + 1 :]
    return lines


def flush_paragraph(buffer: list[str], story: list, styles) -> None:
    if not buffer:
        return
    story.append(Paragraph(clean_inline(" ".join(line.strip() for line in buffer)), styles["BodyText"]))
    story.append(Spacer(1, 0.12 * cm))
    buffer.clear()


def parse_table(table_lines: list[str], styles) -> Table:
    rows = []
    for line in table_lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append([Paragraph(clean_inline(cell), styles["TableCell"]) for cell in cells])

    available_width = A4[0] - 4 * cm
    col_count = max(len(row) for row in rows)
    table = Table(rows, colWidths=[available_width / col_count] * col_count, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9aa7b2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def build_pdf(input_path: Path, output_path: Path) -> None:
    styles = getSampleStyleSheet()
    styles["Title"].fontName = "Helvetica-Bold"
    styles["Title"].fontSize = 20
    styles["Heading1"].fontName = "Helvetica-Bold"
    styles["Heading1"].fontSize = 16
    styles["Heading2"].fontName = "Helvetica-Bold"
    styles["Heading2"].fontSize = 13
    styles["Heading3"].fontName = "Helvetica-Bold"
    styles["Heading3"].fontSize = 11
    styles["BodyText"].fontName = "Helvetica"
    styles["BodyText"].fontSize = 9.5
    styles["BodyText"].leading = 12
    styles["Code"].fontName = "Courier"
    styles["Code"].fontSize = 7
    styles["Code"].leading = 8.2
    styles.add(styles["BodyText"].clone("TableCell", fontSize=7.2, leading=8.4))

    lines = strip_frontmatter(input_path.read_text(encoding="utf-8").splitlines())
    story = []
    paragraph_buffer: list[str] = []
    bullet_buffer: list[str] = []
    table_buffer: list[str] = []
    code_buffer: list[str] = []
    in_code = False

    def flush_bullets() -> None:
        if not bullet_buffer:
            return
        items = [ListItem(Paragraph(clean_inline(item), styles["BodyText"])) for item in bullet_buffer]
        story.append(ListFlowable(items, bulletType="bullet", leftIndent=14))
        story.append(Spacer(1, 0.12 * cm))
        bullet_buffer.clear()

    def flush_table() -> None:
        if not table_buffer:
            return
        story.append(parse_table(table_buffer, styles))
        story.append(Spacer(1, 0.18 * cm))
        table_buffer.clear()

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets()
            flush_table()
            if in_code:
                story.append(Preformatted("\n".join(code_buffer), styles["Code"]))
                story.append(Spacer(1, 0.18 * cm))
                code_buffer.clear()
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_buffer.append(line)
            continue

        if stripped.startswith("|"):
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets()
            table_buffer.append(line)
            continue

        flush_table()

        if not stripped:
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets()
            continue

        if stripped.startswith("# "):
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets()
            story.append(Paragraph(clean_inline(stripped[2:]), styles["Title"]))
            story.append(Spacer(1, 0.25 * cm))
        elif stripped.startswith("## "):
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets()
            story.append(Paragraph(clean_inline(stripped[3:]), styles["Heading1"]))
            story.append(Spacer(1, 0.14 * cm))
        elif stripped.startswith("### "):
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets()
            story.append(Paragraph(clean_inline(stripped[4:]), styles["Heading2"]))
        elif stripped.startswith("- "):
            flush_paragraph(paragraph_buffer, story, styles)
            bullet_buffer.append(stripped[2:])
        elif re.match(r"^\d+\. ", stripped):
            flush_paragraph(paragraph_buffer, story, styles)
            bullet_buffer.append(re.sub(r"^\d+\. ", "", stripped))
        else:
            paragraph_buffer.append(line)

    flush_paragraph(paragraph_buffer, story, styles)
    flush_bullets()
    flush_table()
    if code_buffer:
        story.append(Preformatted("\n".join(code_buffer), styles["Code"]))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    doc.build(story)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: markdown_to_pdf.py INPUT.md OUTPUT.pdf", file=sys.stderr)
        return 2
    build_pdf(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
