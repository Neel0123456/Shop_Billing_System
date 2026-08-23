"""
Manage the Word bill template.

    python manage.py docx_template --create      # write a starter template
    python manage.py docx_template --list        # print every placeholder
    python manage.py docx_template --check       # render it with sample data

The generated template is a *starting point*: open it in Word and restyle it
freely (fonts, borders, logo, column widths). Only the `{{ placeholders }}`
and the `{%tr %}` loop tags need to stay intact.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from billing.services.context import PLACEHOLDER_GROUPS

# Column layout of the line-item table
COLUMNS = [
    ("Sr.", 0.5, "center"),
    ("Item / Description", 2.6, "left"),
    ("Qty", 0.6, "center"),
    ("Rate", 1.0, "right"),
    ("Discount", 1.1, "right"),
    ("Amount", 1.2, "right"),
]


class Command(BaseCommand):
    help = "Create, inspect or verify the Word invoice template."

    def add_arguments(self, parser):
        parser.add_argument("--create", action="store_true", help="Write a starter .docx template.")
        parser.add_argument("--list", action="store_true", help="List every available placeholder.")
        parser.add_argument("--check", action="store_true", help="Render the template with sample data.")
        parser.add_argument("--force", action="store_true", help="Overwrite an existing template.")
        parser.add_argument("--path", help="Write to / read from this path instead of DOCX_TEMPLATE_PATH.")

    def handle(self, *args, **options):
        target = Path(options.get("path") or settings.DOCX_TEMPLATE_PATH)

        if options["list"]:
            return self.list_placeholders()
        if options["check"]:
            return self.check_template(target)
        if options["create"]:
            return self.create_template(target, force=options["force"])

        self.stdout.write(self.style.WARNING("Pass --create, --list or --check."))

    # -- --list ------------------------------------------------------------
    def list_placeholders(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\nPlaceholders for the Word template\n"))
        for group, names in PLACEHOLDER_GROUPS.items():
            self.stdout.write(self.style.MIGRATE_LABEL(f"  {group}"))
            for name in names:
                self.stdout.write(f"    {{{{ {name} }}}}")
            self.stdout.write("")
        self.stdout.write(
            "  Repeat a table row:  put {%tr for item in items %} in a row above\n"
            "                       the data row, and {%tr endfor %} in a row below.\n"
            "  Hide a paragraph:    {%p if has_pending %} ... {%p endif %}\n"
        )

    # -- --check -----------------------------------------------------------
    def check_template(self, target: Path):
        if not target.is_file():
            raise CommandError(f"No template at {target}. Run --create first.")

        from docxtpl import DocxTemplate

        doc = DocxTemplate(str(target))
        declared = doc.get_undeclared_template_variables()

        self.stdout.write(f"Template : {target}")
        self.stdout.write(f"Variables used ({len(declared)}): {', '.join(sorted(declared)) or '-'}")

        known = {n.split(".")[0] for names in PLACEHOLDER_GROUPS.values() for n in names}
        known |= {"items", "item", "has_pending", "has_overpaid", "has_discount",
                  "has_due_date", "has_email", "show_signature_line"}
        unknown = sorted(v for v in declared if v.split(".")[0] not in known)
        if unknown:
            self.stdout.write(
                self.style.WARNING(
                    f"These will render empty (not supplied by the app): {', '.join(unknown)}"
                )
            )

        # Prove it renders with realistic data.
        from billing.tests.factories import sample_context

        doc.render(sample_context())
        out = target.with_name(f"{target.stem}__check.docx")
        doc.save(str(out))
        self.stdout.write(self.style.SUCCESS(f"Rendered OK -> {out}"))

    # -- --create ----------------------------------------------------------
    def create_template(self, target: Path, *, force: bool = False):
        if target.exists() and not force:
            raise CommandError(f"{target} already exists. Pass --force to overwrite.")

        try:
            from docx import Document
            from docx.enum.table import WD_TABLE_ALIGNMENT
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            from docx.shared import Inches, Pt, RGBColor
        except ImportError as exc:
            raise CommandError("python-docx is required: pip install python-docx") from exc

        ALIGN = {
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
        }

        def para(text="", *, size=10, bold=False, align="left", color=None,
                 space_after=2, italic=False, container=None):
            target_container = container if container is not None else doc
            p = target_container.add_paragraph()
            p.alignment = ALIGN[align]
            p.paragraph_format.space_after = Pt(space_after)
            p.paragraph_format.space_before = Pt(0)
            run = p.add_run(text)
            run.font.size = Pt(size)
            run.bold = bold
            run.italic = italic
            if color:
                run.font.color.rgb = RGBColor(*color)
            return p

        def cell_text(cell, text, *, size=9, bold=False, align="left", color=None):
            cell.text = ""
            p = cell.paragraphs[0]
            p.alignment = ALIGN[align]
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.space_before = Pt(1)
            run = p.add_run(text)
            run.font.size = Pt(size)
            run.bold = bold
            if color:
                run.font.color.rgb = RGBColor(*color)

        def shade(cell, hex_colour="EFEFEF"):
            tc_pr = cell._tc.get_or_add_tcPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), hex_colour)
            tc_pr.append(shd)

        def hrule():
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.space_before = Pt(2)
            pbdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "8")
            bottom.set(qn("w:color"), "999999")
            pbdr.append(bottom)
            p._p.get_or_add_pPr().append(pbdr)

        def cond_para(condition, text, *, container=None, **kwargs):
            """
            A paragraph that disappears completely when `condition` is falsy.

            docxtpl's {%p ... %} tag deletes the paragraph it sits in, so the
            `if` and the `endif` MUST live in their own paragraphs - putting
            both into one paragraph deletes it and leaves Jinja unbalanced.
            """
            para("{%p if " + condition + " %}", size=1, space_after=0, container=container)
            body = para(text, container=container, **kwargs)
            para("{%p endif %}", size=1, space_after=0, container=container)
            return body

        # ---------------- build the document ----------------
        doc = Document()

        section = doc.sections[0]
        section.page_width = Inches(8.27)   # A4
        section.page_height = Inches(11.69)
        for attr in ("left_margin", "right_margin"):
            setattr(section, attr, Inches(0.6))
        section.top_margin = Inches(0.5)
        section.bottom_margin = Inches(0.5)

        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(10)

        # --- letterhead ---------------------------------------------------
        para("{{ shop_name }}", size=20, bold=True, align="center", space_after=0)
        cond_para("shop_tagline", "{{ shop_tagline }}",
                  size=9, align="center", italic=True, space_after=0)
        para("{{ shop_address }}", size=9, align="center", space_after=0)
        # Inline {% if %} is safe: it opens and closes inside one paragraph.
        para("Phone: {{ shop_phone }}{% if shop_email %}  |  Email: {{ shop_email }}{% endif %}",
             size=9, align="center", space_after=0)
        cond_para("shop_gstin", "GSTIN: {{ shop_gstin }}",
                  size=9, align="center", space_after=4)
        hrule()
        para("INVOICE", size=13, bold=True, align="center", space_after=6)

        # --- customer / bill meta ----------------------------------------
        meta = doc.add_table(rows=1, cols=2)
        meta.alignment = WD_TABLE_ALIGNMENT.CENTER
        meta.autofit = False
        left, right = meta.rows[0].cells
        left.width = Inches(4.2)
        right.width = Inches(2.8)

        cell_text(left, "BILL TO", size=9, bold=True)
        para("{{ customer_name }}", size=9, bold=True, container=left, space_after=0)
        para("Phone: {{ customer_phone }}", size=9, container=left, space_after=0)
        cond_para("has_email", "Email: {{ customer_email }}",
                  size=9, container=left, space_after=0)
        para("City: {{ customer_city }}", size=9, container=left, space_after=0)

        # The bill date always prints, right beside the bill number.
        cell_text(right, "Bill No: {{ invoice_number }}", size=9, bold=True, align="right")
        para("Date: {{ invoice_date }}", size=9, bold=True, align="right",
             container=right, space_after=0)
        cond_para("printed_by", "Billed by: {{ printed_by }}",
                  size=8, align="right", container=right, space_after=0)

        doc.add_paragraph().paragraph_format.space_after = Pt(2)

        # --- line items ---------------------------------------------------
        table = doc.add_table(rows=3, cols=len(COLUMNS))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False

        header_row, loop_row, end_row = table.rows[0], table.rows[1], table.rows[2]

        for idx, (title, width, align) in enumerate(COLUMNS):
            cell = header_row.cells[idx]
            cell.width = Inches(width)
            cell_text(cell, title, size=9, bold=True, align=align)
            shade(cell)

        # docxtpl repeats the row *between* the {%tr %} tags.
        cell_text(loop_row.cells[0], "{%tr for item in items %}", size=8)
        for idx in range(1, len(COLUMNS)):
            cell_text(loop_row.cells[idx], "", size=8)

        data_row = table.add_row()
        values = [
            "{{ item.sr }}",
            "{{ item.description }}",
            "{{ item.qty }}{% if item.unit %} {{ item.unit }}{% endif %}",
            "{{ item.rate }}",
            "{{ item.discount_label }}",
            "{{ item.amount }}",
        ]
        for idx, ((_, width, align), value) in enumerate(zip(COLUMNS, values)):
            cell = data_row.cells[idx]
            cell.width = Inches(width)
            cell_text(cell, value, size=9, align=align)

        # Move the data row between the loop tags, then close the loop.
        loop_row._tr.addnext(data_row._tr)
        cell_text(end_row.cells[0], "{%tr endfor %}", size=8)
        for idx in range(1, len(COLUMNS)):
            cell_text(end_row.cells[idx], "", size=8)
        data_row._tr.addnext(end_row._tr)

        # --- totals -------------------------------------------------------
        doc.add_paragraph().paragraph_format.space_after = Pt(0)
        totals = doc.add_table(rows=0, cols=2)
        totals.alignment = WD_TABLE_ALIGNMENT.RIGHT
        totals.autofit = False

        def totals_row(label, value, *, bold=False, shaded=False):
            row = totals.add_row()
            label_cell, value_cell = row.cells
            label_cell.width = Inches(2.2)
            value_cell.width = Inches(1.4)
            cell_text(label_cell, label, size=10, bold=bold, align="right")
            cell_text(value_cell, value, size=10, bold=bold, align="right")
            if shaded:
                shade(label_cell, "E8E8E8")
                shade(value_cell, "E8E8E8")

        totals_row("Subtotal", "{{ currency }} {{ subtotal }}")
        totals_row(
            "{% if has_discount %}Total discount{% endif %}",
            "{% if has_discount %}- {{ currency }} {{ total_discount }}{% endif %}",
        )
        totals_row("TOTAL PAYABLE", "{{ currency }} {{ grand_total }}", bold=True, shaded=True)
        totals_row("Amount received", "{{ currency }} {{ paid_amount }}")
        totals_row(
            "{% if has_pending %}Balance pending{% endif %}",
            "{% if has_pending %}{{ currency }} {{ pending_amount }}{% endif %}",
            bold=True,
        )
        totals_row(
            "{% if has_overpaid %}Overpaid (to return){% endif %}",
            "{% if has_overpaid %}{{ currency }} {{ overpaid_amount }}{% endif %}",
            bold=True,
        )

        # --- words, payment state, footer ---------------------------------
        para("Amount in words: {{ grand_total_words }}", size=9, bold=True, space_after=4)
        para("Payment status: {{ payment_status_label }}", size=9, space_after=0)
        cond_para("has_due_date", "Payment due by: {{ due_date }}",
                  size=9, bold=True, color=(0xB4, 0x23, 0x18), space_after=0)
        cond_para("notes", "Note: {{ notes }}", size=9, space_after=4)

        hrule()
        cond_para("shop_terms", "{{ shop_terms }}", size=8, italic=True, space_after=6)

        sign = doc.add_table(rows=1, cols=2)
        sign.autofit = False
        sign_left, sign_right = sign.rows[0].cells
        sign_left.width = Inches(3.5)
        sign_right.width = Inches(3.5)
        cell_text(sign_left, "Customer signature", size=8, align="left")
        cell_text(
            sign_right,
            "{% if show_signature_line %}For {{ shop_name }}{% endif %}",
            size=8,
            align="right",
        )

        para("{{ shop_footer_note }}", size=9, align="center", bold=True, space_after=0)
        para(
            "Bill dated {{ invoice_date }}  |  entered {{ created_at }}"
            "  |  printed {{ printed_on }}",
            size=7, align="center", color=(0x88, 0x88, 0x88),
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(target))

        self.stdout.write(self.style.SUCCESS(f"Starter Word template written to {target}"))
        self.stdout.write(
            "\nNext steps:\n"
            "  1. Open it in Word and restyle it to match the shop's bill format.\n"
            "  2. Keep the {{ placeholders }} and the {%tr for item in items %} /\n"
            "     {%tr endfor %} rows intact.\n"
            "  3. Verify with:  python manage.py docx_template --check\n"
            "  4. See every placeholder with:  python manage.py docx_template --list\n"
        )
