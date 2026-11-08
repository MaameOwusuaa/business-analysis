"""Render docs/architecture.md to docs/architecture_note.pdf."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer  # noqa: E402

from insightforge.reporting.report import H1, H2, markdown_flowables  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs"
AUTHOR = "Christokin Christopherson"


def main() -> None:
    md = (DOCS / "architecture.md").read_text()
    story = []
    for block in re.split(r"\n(?=## |# |!\[)", md):
        first = block.strip().splitlines()[0] if block.strip() else ""
        rest = "\n".join(block.strip().splitlines()[1:])
        if first.startswith("# "):
            story.append(Paragraph(first[2:], H1))
        elif first.startswith("## "):
            story.append(Paragraph(first[3:], H2))
        elif first.startswith("!["):
            src = re.search(r"\((.+?)\)", first).group(1)
            from reportlab.lib.utils import ImageReader

            iw, ih = ImageReader(str(DOCS / src)).getSize()
            img = Image(str(DOCS / src), width=180 * mm, height=180 * mm * ih / iw)
            story += [img, Spacer(1, 4)]
        else:
            rest = block
        story += markdown_flowables(rest)
    out = DOCS / "architecture_note.pdf"
    SimpleDocTemplate(str(out), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=13 * mm,
                      bottomMargin=13 * mm, title="InsightForge architecture note", author=AUTHOR).build(story)
    print("wrote", out)


if __name__ == "__main__":
    main()
