"""
app/quotation_service.py
Generates PDF quotations and sends them via WhatsApp/Email.
"""
import os
import json
import asyncio
from datetime import datetime, timedelta
from loguru import logger
from app import tenant_db as tdb
from app.communication_service import send_whatsapp, send_email


def _logo_path(tenant_id: int) -> str:
    path = f"static/tenant_logos/{tenant_id}.png"
    return path if os.path.exists(path) else ""


def generate_pdf(quotation: dict, tenant_config: dict) -> str:
    """Generate PDF quotation using ReportLab. Returns file path."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph,
            Spacer, Image, HRFlowable
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

        os.makedirs(f"quotations/{quotation['tenant_id']}", exist_ok=True)
        pdf_path = f"quotations/{quotation['tenant_id']}/{quotation['quote_number']}.pdf"

        doc    = SimpleDocTemplate(pdf_path, pagesize=A4,
                                   rightMargin=15*mm, leftMargin=15*mm,
                                   topMargin=15*mm, bottomMargin=15*mm)
        styles = getSampleStyleSheet()
        story  = []

        brand_color = tenant_config.get("brand_color") or "#1a1a2e"
        try:
            r = int(brand_color[1:3], 16) / 255
            g = int(brand_color[3:5], 16) / 255
            b = int(brand_color[5:7], 16) / 255
            brand_rgb = colors.Color(r, g, b)
        except Exception:
            brand_rgb = colors.HexColor("#1a1a2e")

        # ── Header ────────────────────────────────────────────
        logo = _logo_path(quotation["tenant_id"])
        header_data = []
        if logo:
            header_data = [[Image(logo, width=40*mm, height=15*mm),
                           Paragraph(f"""
                               <font size=9 color='#666666'>
                               {tenant_config.get('company_name','')}<br/>
                               {tenant_config.get('company_website','')}<br/>
                               </font>""", styles["Normal"])]]
        else:
            header_data = [[
                Paragraph(f"<font size=16 color='#{brand_color.lstrip('#')}'><b>{tenant_config.get('company_name','')}</b></font>",
                          styles["Normal"]),
                Paragraph(f"<font size=9 color='#666666'>{tenant_config.get('company_website','')}</font>",
                          styles["Normal"])
            ]]

        header_table = Table(header_data, colWidths=[90*mm, 90*mm])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",  (1, 0), (1, 0),   "RIGHT"),
        ]))
        story.append(header_table)
        story.append(HRFlowable(width="100%", thickness=2, color=brand_rgb))
        story.append(Spacer(1, 5*mm))

        # ── Quotation title + meta ─────────────────────────────
        story.append(Paragraph(
            f"<font size=18 color='#{brand_color.lstrip('#')}'><b>QUOTATION</b></font>",
            styles["Normal"]
        ))
        story.append(Spacer(1, 2*mm))

        valid_until = (datetime.now() + timedelta(days=quotation.get("valid_days", 7))).strftime("%d %B %Y")
        meta = [
            ["Quotation No:", quotation.get("quote_number", "")],
            ["Date:",         datetime.now().strftime("%d %B %Y")],
            ["Valid Until:",  valid_until],
        ]
        meta_table = Table(meta, colWidths=[35*mm, 80*mm])
        meta_table.setStyle(TableStyle([
            ("FONTSIZE",  (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (0, -1),  colors.HexColor("#666666")),
            ("FONTNAME",  (1, 0), (1, -1),  "Helvetica-Bold"),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 6*mm))

        # ── Bill to ───────────────────────────────────────────
        story.append(Paragraph(
            "<font size=10 color='#666666'><b>BILL TO</b></font>",
            styles["Normal"]
        ))
        story.append(Spacer(1, 1*mm))
        story.append(Paragraph(
            f"<b>{quotation.get('customer_name','')}</b><br/>"
            f"{quotation.get('customer_company','')}<br/>"
            f"{quotation.get('customer_phone','')}<br/>"
            f"{quotation.get('customer_email','')}",
            styles["Normal"]
        ))
        story.append(Spacer(1, 6*mm))

        # ── Items table ───────────────────────────────────────
        items = (
            json.loads(quotation.get("items", "[]"))
            if isinstance(quotation.get("items"), str)
            else quotation.get("items", [])
        )

        table_data = [["#", "Product / Service", "Qty", "Unit Price", "Amount"]]
        for i, item in enumerate(items, 1):
            qty   = item.get("qty", 1)
            price = item.get("price", 0)
            table_data.append([
                str(i),
                item.get("name", ""),
                str(qty),
                f"₹{price:,.2f}",
                f"₹{qty * price:,.2f}",
            ])

        items_table = Table(table_data,
                            colWidths=[10*mm, 90*mm, 15*mm, 30*mm, 30*mm])
        items_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  brand_rgb),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("ALIGN",         (2, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f8f8")]),
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(items_table)
        story.append(Spacer(1, 4*mm))

        # ── Totals ────────────────────────────────────────────
        subtotal   = quotation.get("subtotal", 0)
        tax_pct    = quotation.get("tax_percent", 18)
        tax_amount = quotation.get("tax_amount", 0)
        total      = quotation.get("total_amount", 0)

        totals = [
            ["", "", "Subtotal:",         f"₹{subtotal:,.2f}"],
            ["", "", f"GST ({tax_pct}%):", f"₹{tax_amount:,.2f}"],
            ["", "", "TOTAL:",             f"₹{total:,.2f}"],
        ]
        totals_table = Table(totals, colWidths=[10*mm, 90*mm, 45*mm, 30*mm])
        totals_table.setStyle(TableStyle([
            ("ALIGN",     (2, 0), (-1, -1), "RIGHT"),
            ("FONTSIZE",  (0, 0), (-1, -1), 9),
            ("FONTNAME",  (2, 2), (-1, 2),  "Helvetica-Bold"),
            ("FONTSIZE",  (2, 2), (-1, 2),  11),
            ("TEXTCOLOR", (2, 2), (-1, 2),  brand_rgb),
            ("LINEABOVE", (2, 2), (-1, 2),  1, colors.HexColor("#dddddd")),
        ]))
        story.append(totals_table)

        # ── Notes ─────────────────────────────────────────────
        notes = quotation.get("notes", "") or tenant_config.get("quotation_notes", "")
        if notes:
            story.append(Spacer(1, 6*mm))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=colors.HexColor("#dddddd")))
            story.append(Spacer(1, 2*mm))
            story.append(Paragraph(
                f"<font size=9 color='#666666'><b>Terms & Notes:</b><br/>{notes}</font>",
                styles["Normal"]
            ))

        # ── Footer ────────────────────────────────────────────
        story.append(Spacer(1, 8*mm))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#dddddd")))
        story.append(Paragraph(
            f"<font size=8 color='#999999'>Generated by DialBot · {tenant_config.get('company_name','')} · {datetime.now().strftime('%d %b %Y %H:%M')}</font>",
            ParagraphStyle("footer", parent=styles["Normal"], alignment=TA_CENTER)
        ))

        doc.build(story)
        logger.info(f"[Quotation] PDF generated: {pdf_path}")
        return pdf_path

    except ImportError:
        logger.error("[Quotation] ReportLab not installed — run: pip install reportlab")
        return ""
    except Exception as e:
        logger.error(f"[Quotation] PDF generation error: {e}")
        return ""


async def send_quotation(
    tenant_id: int,
    lead: dict,
    items: list,
    call_id: int = None,
    send_via: str = "both",
) -> dict:
    """
    Create quotation record, generate PDF, send via WhatsApp/Email.
    Returns: {"quote_id": int, "quote_number": str, "pdf_path": str}
    """
    tenant_config = tdb.get_tenant_config(tenant_id) or {}

    # Master switches — respect tenant channel toggles
    wa_enabled    = bool(tenant_config.get("whatsapp_enabled", 0))
    email_enabled = bool(tenant_config.get("email_enabled", 1))
    if send_via == "both":
        if wa_enabled and not email_enabled:
            send_via = "whatsapp"
        elif email_enabled and not wa_enabled:
            send_via = "email"
        elif not wa_enabled and not email_enabled:
            logger.warning(f"[Quotation] All channels disabled — not sending (tenant={tenant_id})")
            send_via = "none"

    # Calculate totals
    subtotal   = sum(i.get("qty", 1) * i.get("price", 0) for i in items)
    tax_pct    = float(tenant_config.get("quotation_tax_percent") or 18)
    tax_amount = round(subtotal * tax_pct / 100, 2)
    total      = round(subtotal + tax_amount, 2)

    # Create DB record
    quote_data = {
        "lead_id":          lead.get("id"),
        "call_id":          call_id,
        "customer_name":    lead.get("name") or lead.get("lead_name", ""),
        "customer_phone":   lead.get("phone", ""),
        "customer_email":   lead.get("email", ""),
        "customer_company": lead.get("company", ""),
        "items":            items,
        "subtotal":         subtotal,
        "tax_percent":      tax_pct,
        "tax_amount":       tax_amount,
        "total_amount":     total,
        "valid_days":       int(tenant_config.get("quotation_valid_days") or 7),
        "notes":            tenant_config.get("quotation_notes", ""),
        "sent_via":         send_via,
    }
    quote_id = tdb.create_quotation(tenant_id, quote_data)
    quotation = {
        "id": quote_id,
        "tenant_id": tenant_id,
        **quote_data,
        "quote_number": f"QT-{datetime.now().strftime('%Y%m%d')}-{tenant_id:03d}-{quote_id:04d}",
    }

    # Generate PDF
    pdf_path = generate_pdf(quotation, tenant_config)
    if pdf_path:
        tdb.update_quotation_status(quote_id, "sent", pdf_path)

    # Build message
    items_text = "\n".join(
        f"• {i.get('name','')} × {i.get('qty',1)} = ₹{i.get('qty',1)*i.get('price',0):,.0f}"
        for i in items
    )
    wa_message = (
        f"नमस्ते {quotation['customer_name']} जी! 🙏\n\n"
        f"*{tenant_config.get('company_name','')}* की तरफ से आपकी quotation:\n\n"
        f"{items_text}\n\n"
        f"*Subtotal: ₹{subtotal:,.0f}*\n"
        f"*GST ({tax_pct}%): ₹{tax_amount:,.0f}*\n"
        f"*Total: ₹{total:,.0f}*\n\n"
        f"Quote No: {quotation['quote_number']}\n"
        f"Valid for {quotation['valid_days']} days\n\n"
        f"कोई सवाल हो तो हमसे संपर्क करें। धन्यवाद! 🙏"
    )

    tasks = []
    if send_via in ("whatsapp", "both") and lead.get("phone"):
        tasks.append(send_whatsapp(lead["phone"], wa_message, tenant_config))
    if send_via in ("email", "both") and lead.get("email"):
        email_body = wa_message.replace("*", "").replace("🙏", "").replace("•", "-")
        tasks.append(send_email(
            lead["email"],
            f"Quotation {quotation['quote_number']} from {tenant_config.get('company_name','')}",
            email_body,
            tenant_config
        ))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    logger.info(f"[Quotation] Sent quote_id={quote_id} to {lead.get('phone')} via {send_via}")
    return {"quote_id": quote_id, "quote_number": quotation["quote_number"], "pdf_path": pdf_path}
