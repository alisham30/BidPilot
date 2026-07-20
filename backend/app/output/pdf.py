"""Submission-ready bid PDF (ReportLab) — cover, SKU tables, price schedule,
clause-level deviation statement. Generated ONLY after a human approve decision
(enforced at the API layer)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from ..config import PDF_OUT_DIR
from ..db import RFP
from ..schemas import DraftResponse
from .deviation import deviation_clauses

INK = colors.HexColor("#111111")
RULE = colors.HexColor("#333333")
HEAD_BG = colors.HexColor("#efefef")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("CoverTitle", parent=ss["Title"], fontName="Times-Bold",
                          fontSize=20, textColor=INK, spaceAfter=6))
    ss.add(ParagraphStyle("CoverSub", parent=ss["Heading2"], fontName="Times-Roman",
                          fontSize=13, textColor=INK, spaceAfter=4))
    ss.add(ParagraphStyle("H", parent=ss["Heading2"], fontName="Times-Bold",
                          fontSize=12, textColor=INK, spaceBefore=14, spaceAfter=4))
    ss.add(ParagraphStyle("Small", parent=ss["BodyText"], fontSize=8, leading=10))
    ss["BodyText"].fontName = "Times-Roman"
    ss["BodyText"].fontSize = 9.5
    ss["Heading4"].fontName = "Times-Bold"
    ss["Heading4"].textColor = INK
    return ss


def _table(data, col_widths=None, small=False):
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5 if small else 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, RULE),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9a9a9a")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return t


def generate_bid_pdf(rfp: RFP, draft: DraftResponse) -> Path:
    PDF_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = PDF_OUT_DIR / f"{rfp.rfp_id}_bid.pdf"
    ss = _styles()
    story = []

    # ---- cover (formal letter-style, no colour) ----
    story.append(Spacer(1, 45 * mm))
    story.append(Paragraph("TECHNICAL & COMMERCIAL BID", ss["CoverTitle"]))
    from reportlab.platypus import HRFlowable
    story.append(HRFlowable(width="100%", thickness=1, color=RULE, spaceAfter=6))
    story.append(Paragraph(rfp.title or "(untitled tender)", ss["CoverSub"]))
    story.append(Spacer(1, 10 * mm))
    for label, value in [
        ("To", rfp.issuer or "—"),
        ("Tender Reference", rfp.reference_no or "—"),
        ("Bid Due Date", rfp.due_date.isoformat() if rfp.due_date else "—"),
        ("Date of Offer", date.today().isoformat()),
    ]:
        story.append(Paragraph(f"<b>{label}:</b>&nbsp;&nbsp;{value}", ss["BodyText"]))
    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph(
        "Dear Sir / Madam,<br/><br/>"
        "With reference to the above tender, we are pleased to submit our technical and "
        "commercial offer for the supply of the listed items. The offered products, "
        "parameter-level compliance, price schedule and statement of deviations are "
        "enclosed in the sections that follow.", ss["BodyText"]))
    story.append(PageBreak())

    # ---- offered products + comparison ----
    story.append(Paragraph("1. Offered Products", ss["H"]))
    head = ["Item", "Description", "Qty", "Offered SKU", "Spec Match %"]
    rows = [head]
    for item in draft.sku_table.items:
        best = item.top3[0] if item.top3 else None
        rows.append([
            item.item_no,
            Paragraph(item.description, ss["Small"]),
            f"{item.quantity:g} {item.unit}",
            item.top_pick or "—",
            f"{best.pct:.1f}%" if best else "—",
        ])
    story.append(_table(rows, col_widths=[16 * mm, 72 * mm, 24 * mm, 34 * mm, 24 * mm]))

    story.append(Paragraph("2. Parameter-level Compliance", ss["H"]))
    for item in draft.sku_table.items:
        best = item.top3[0] if item.top3 else None
        if best is None:
            continue
        story.append(Paragraph(f"Item {item.item_no} — offered {item.top_pick or '—'}", ss["Heading4"]))
        comp = [["Parameter", "Tender requirement", "Offered value", "Score"]]
        for e in best.evidence:
            comp.append([e.param, e.required, e.actual or "—", f"{e.score:.2f}"])
        story.append(_table(comp, col_widths=[40 * mm, 50 * mm, 50 * mm, 18 * mm], small=True))
        story.append(Spacer(1, 3 * mm))
    story.append(PageBreak())

    # ---- price schedule ----
    story.append(Paragraph("3. Price Schedule", ss["H"]))
    price_rows = [["Item", "SKU", "Qty", "Unit rate", "Amount"]]
    for line in draft.price_table.lines:
        price_rows.append([
            line.item_no, line.sku_id or "—", f"{line.quantity:g} {line.unit}",
            f"{line.unit_price:,.2f} {line.currency}" if line.priced else "NOT PRICED",
            f"{line.amount:,.2f}" if line.priced else "—",
        ])
    story.append(_table(price_rows, col_widths=[16 * mm, 36 * mm, 30 * mm, 44 * mm, 34 * mm]))
    story.append(Spacer(1, 4 * mm))

    if draft.price_table.test_lines:
        test_rows = [["Test / acceptance", "Standard", "Price"]]
        for t in draft.price_table.test_lines:
            test_rows.append([t.test_name, t.standard or "—",
                              f"{t.price:,.2f} {t.currency}" if t.priced else "NOT PRICED"])
        story.append(_table(test_rows, col_widths=[80 * mm, 45 * mm, 35 * mm]))
        story.append(Spacer(1, 4 * mm))

    totals = [
        ["Material total", f"{draft.price_table.material_total:,.2f} {draft.price_table.currency}"],
        ["Testing total", f"{draft.price_table.test_total:,.2f} {draft.price_table.currency}"],
        ["Grand total", f"{draft.price_table.grand_total:,.2f} {draft.price_table.currency}"],
    ]
    story.append(_table([["", ""]] + totals, col_widths=[80 * mm, 80 * mm]))

    # ---- deviation statement ----
    story.append(Paragraph("4. Deviation Statement", ss["H"]))
    for clause in deviation_clauses(draft.sku_table):
        story.append(Paragraph("• " + clause, ss["BodyText"]))

    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Bid — {rfp.title}")
    doc.build(story)
    return out
