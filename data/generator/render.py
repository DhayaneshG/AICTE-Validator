"""PDF rendering for synthetic EoA documents (ReportLab).

Four layout families (L1..L4) x three font families give varied digital PDFs with a real text layer.
Every page carries a "SYNTHETIC TEST DOCUMENT" footer. Output bytes are deterministic for a
given (spec, layout, font, creation timestamp) because ReportLab runs with invariant=1 and TZ=UTC.
"""
from __future__ import annotations

import io
import os
import time

os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

W, H = A4
FONTS = {
    "sans": ("Helvetica", "Helvetica-Bold"),
    "serif": ("Times-Roman", "Times-Bold"),
    "mono": ("Courier", "Courier-Bold"),
}
FOOTER = "SYNTHETIC TEST DOCUMENT - generated for software evaluation - not a real record"
GREY = colors.Color(0.4, 0.4, 0.4)
LIGHT = colors.Color(0.93, 0.94, 0.96)


class Pen:
    """Cursor-based text writer with automatic page breaks."""

    def __init__(self, c, fontkey, size, margin=50, x0=None, x1=None):
        self.c = c
        self.reg, self.bold = FONTS[fontkey]
        self.size = size
        self.m = margin
        self.x0 = margin if x0 is None else x0
        self.x1 = W - margin if x1 is None else x1
        self.page = 1
        self.y = H - margin
        self.on_new_page = None

    # -- page handling
    def footer(self):
        self.c.setFont(self.reg, 7)
        self.c.setFillColor(GREY)
        self.c.drawCentredString(W / 2, 28, f"{FOOTER}  |  page {self.page}")
        self.c.setFillColor(colors.black)

    def newpage(self):
        self.footer()
        self.c.showPage()
        self.page += 1
        self.y = H - self.m
        if self.on_new_page:
            self.on_new_page(self)

    def need(self, h):
        if self.y - h < self.m + 25:
            self.newpage()

    # -- text primitives
    def lead(self, size=None):
        return (size or self.size) * 1.35

    def text(self, s, x, font=None, size=None, color=colors.black):
        self.c.setFont(font or self.reg, size or self.size)
        self.c.setFillColor(color)
        self.c.drawString(x, self.y, s)
        self.c.setFillColor(colors.black)

    def line(self, s, x=None, font=None, size=None, color=colors.black, align="left", adv=True):
        size = size or self.size
        font = font or self.reg
        self.need(self.lead(size))
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        if align == "center":
            self.c.drawCentredString((self.x0 + self.x1) / 2, self.y, s)
        elif align == "right":
            self.c.drawRightString(self.x1, self.y, s)
        else:
            self.c.drawString(self.x0 if x is None else x, self.y, s)
        self.c.setFillColor(colors.black)
        if adv:
            self.y -= self.lead(size)

    def para(self, s, x=None, width=None, font=None, size=None, gap=4):
        size = size or self.size
        font = font or self.reg
        x = self.x0 if x is None else x
        width = width or (self.x1 - x)
        for ln in simpleSplit(s, font, size, width):
            self.line(ln, x=x, font=font, size=size)
        self.y -= gap

    def gap(self, n):
        self.y -= n


def _draw_qr(c, qr_img, x, y, size=88):
    if qr_img is None:
        return
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), x, y, size, size)
    c.setFont("Helvetica", 6)
    c.drawString(x, y - 8, "Scan to verify (MOCK issuer)")


def _sign_block(pen, spec, x=None):
    x = pen.x1 - 170 if x is None else x
    name, desig = spec["signer"]
    pen.c.setFont(pen.reg, pen.size - 1)
    pen.c.line(x, pen.y, x + 160, pen.y)
    pen.y -= pen.lead()
    pen.text(name, x, font=pen.bold, size=pen.size - 1)
    pen.y -= pen.lead()
    pen.text(desig, x, size=pen.size - 2)
    pen.y -= pen.lead()


def _fields_colon(pen, fields, x, labw):
    for label, val in fields:
        pen.need(pen.lead() + 2)
        pen.text(label + " :", x, font=pen.bold)
        lines = simpleSplit(val, pen.reg, pen.size, pen.x1 - x - labw)
        for i, ln in enumerate(lines):
            if i:
                pen.y -= pen.lead()
                pen.need(pen.lead())
            pen.text(ln, x + labw)
        pen.y -= pen.lead() + 1


# ---------------------------------------------------------------- layouts
def layout_L1(pen, spec, qr):
    """Centered letterhead, 'Label : value' lines, QR bottom-left, signature bottom-right."""
    for i, ln in enumerate(spec["issuer"]):
        pen.line(ln, font=pen.bold, size=pen.size + (3 if i == 0 else 0), align="center")
    pen.c.line(pen.x0, pen.y + 4, pen.x1, pen.y + 4)
    pen.gap(10)
    pen.line(spec["title"], font=pen.bold, size=pen.size + 4, align="center")
    pen.gap(8)
    for p in spec["pre"]:
        pen.para(p)
    pen.gap(4)
    labw = max(pen.c.stringWidth(l + " :", pen.bold, pen.size) for l, _ in spec["fields"]) + 12
    _fields_colon(pen, spec["fields"], pen.x0 + 8, labw)
    pen.gap(6)
    for p in spec["post"]:
        pen.para(p)
    pen.need(125)
    pen.gap(8)
    top = pen.y
    if qr is not None:
        _draw_qr(pen.c, qr, pen.x0, top - 92)
    _sign_block(pen, spec)
    pen.y = min(pen.y, top - 110)


def layout_L2(pen, spec, qr):
    """Left-aligned header, QR top-right, bordered label|value table."""
    top = pen.y
    if qr is not None:
        _draw_qr(pen.c, qr, pen.x1 - 88, top - 80)
    hw = pen.x1 - pen.x0 - 100
    for i, ln in enumerate(spec["issuer"]):
        for sub in simpleSplit(ln, pen.bold, pen.size + (2 if i == 0 else 0), hw):
            pen.line(sub, font=pen.bold, size=pen.size + (2 if i == 0 else 0))
    pen.y = min(pen.y, top - 100)
    pen.c.line(pen.x0, pen.y + 6, pen.x1, pen.y + 6)
    pen.gap(8)
    pen.line(spec["title"], font=pen.bold, size=pen.size + 3)
    pen.gap(6)
    colw = 150
    rowh = pen.lead() + 7
    for label, val in spec["fields"]:
        lines = simpleSplit(val, pen.reg, pen.size, pen.x1 - pen.x0 - colw - 12)
        h = rowh + (len(lines) - 1) * pen.lead()
        pen.need(h + 2)
        y0 = pen.y - h + pen.lead() - 3
        pen.c.setFillColor(LIGHT)
        pen.c.rect(pen.x0, y0, colw, h, fill=1, stroke=0)
        pen.c.setFillColor(colors.black)
        pen.c.rect(pen.x0, y0, pen.x1 - pen.x0, h, fill=0, stroke=1)
        pen.c.line(pen.x0 + colw, y0, pen.x0 + colw, y0 + h)
        pen.text(label, pen.x0 + 5, font=pen.bold)
        for i, ln in enumerate(lines):
            pen.text(ln, pen.x0 + colw + 6)
            if i < len(lines) - 1:
                pen.y -= pen.lead()
        pen.y -= pen.lead() + 7
    pen.gap(6)
    for p in spec["pre"] + spec["post"]:
        pen.para(p)
    pen.need(70)
    pen.gap(14)
    _sign_block(pen, spec)


def layout_L3(pen, spec, qr):
    """Boxed grid of stacked label/value cells; QR bottom-left."""
    for i, ln in enumerate(spec["issuer"]):
        pen.line(ln, font=pen.bold, size=pen.size + (2 if i == 0 else 0))
    pen.gap(4)
    pen.c.setLineWidth(1.5)
    pen.c.line(pen.x0, pen.y + 6, pen.x1, pen.y + 6)
    pen.c.setLineWidth(1)
    pen.gap(8)
    pen.line(spec["title"].upper(), font=pen.bold, size=pen.size + 3, align="center")
    pen.gap(8)
    for p in spec["pre"]:
        pen.para(p)
    pen.gap(2)
    colw = (pen.x1 - pen.x0 - 8) / 2
    fields = list(spec["fields"])
    i = 0
    while i < len(fields):
        row = fields[i:i + 2]
        heights = []
        for _, val in row:
            heights.append(len(simpleSplit(val, pen.bold, pen.size, colw - 12)))
        h = 14 + max(heights) * pen.lead() + 8
        pen.need(h + 4)
        top = pen.y
        for j, (label, val) in enumerate(row):
            bx = pen.x0 + j * (colw + 8)
            pen.c.rect(bx, top - h + 8, colw, h, fill=0, stroke=1)
            pen.c.setFont(pen.reg, 7)
            pen.c.setFillColor(GREY)
            pen.c.drawString(bx + 5, top - 4, label.upper())
            pen.c.setFillColor(colors.black)
            yy = top - 4 - pen.lead()
            for ln in simpleSplit(val, pen.bold, pen.size, colw - 12):
                pen.c.setFont(pen.bold, pen.size)
                pen.c.drawString(bx + 5, yy, ln)
                yy -= pen.lead()
        pen.y = top - h - 2
        i += 2
    pen.gap(6)
    for p in spec["post"]:
        pen.para(p)
    pen.need(125)
    pen.gap(8)
    top = pen.y
    if qr is not None:
        _draw_qr(pen.c, qr, pen.x0 + 4, top - 92)
    _sign_block(pen, spec)
    pen.y = min(pen.y, top - 110)


def layout_L4(pen, spec, qr):
    """Shaded sidebar with stacked label/value; main column with narrative; QR in sidebar foot."""
    side_w = 160
    main_x = pen.x0 + side_w + 14

    def draw_sidebar_bg(p):
        p.c.setFillColor(LIGHT)
        p.c.rect(p.m - 8, 50, side_w + 8, H - 100, fill=1, stroke=0)
        p.c.setFillColor(colors.black)

    draw_sidebar_bg(pen)
    pen.on_new_page = lambda p: draw_sidebar_bg(p)
    top = pen.y
    c = pen.c
    sy = top - 6
    for label, val in spec["fields"]:
        c.setFont(pen.reg, 7)
        c.setFillColor(GREY)
        c.drawString(pen.x0, sy, label.upper())
        c.setFillColor(colors.black)
        sy -= 11
        for ln in simpleSplit(val, pen.bold, pen.size - 1, side_w - 10):
            c.setFont(pen.bold, pen.size - 1)
            c.drawString(pen.x0, sy, ln)
            sy -= pen.lead(pen.size - 1)
        sy -= 9
    if qr is not None:
        _draw_qr(c, qr, pen.x0, 92)
    # main column
    pen.x0, saved_x0 = main_x, pen.x0
    for i, ln in enumerate(spec["issuer"]):
        for sub in simpleSplit(ln, pen.bold, pen.size + (2 if i == 0 else 0), pen.x1 - pen.x0):
            pen.line(sub, font=pen.bold, size=pen.size + (2 if i == 0 else 0))
    pen.c.line(pen.x0, pen.y + 6, pen.x1, pen.y + 6)
    pen.gap(8)
    for sub in simpleSplit(spec["title"], pen.bold, pen.size + 3, pen.x1 - pen.x0):
        pen.line(sub, font=pen.bold, size=pen.size + 3)
    pen.gap(6)
    for p in spec["pre"] + spec["post"]:
        pen.para(p)
    pen.need(70)
    pen.gap(14)
    _sign_block(pen, spec, x=pen.x1 - 150)
    pen.x0 = saved_x0


LAYOUTS = {"L1": layout_L1, "L2": layout_L2, "L3": layout_L3, "L4": layout_L4}


def render_pdf(spec: dict, layout: str, fontkey: str, size: float, created_ts: float, qr_img=None) -> bytes:
    """Render one document spec to PDF bytes with the given creation timestamp (epoch seconds, UTC)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1, pageCompression=1)
    _dtv = __import__("datetime").datetime.fromtimestamp(float(created_ts), __import__("datetime").timezone.utc)
    pdf_dt = "D:%04d%02d%02d%02d%02d%02d+00'00'" % (_dtv.year, _dtv.month, _dtv.day, _dtv.hour, _dtv.minute, _dtv.second)
    c._doc.info._dateFormatter = lambda *a: pdf_dt  # explicit CreationDate/ModDate (UTC)
    c.setTitle(spec["meta_title"])
    c.setAuthor(spec["meta_author"])
    c.setSubject(spec["title"])
    c.setCreator("Synthetic EoA generator (MOCK data)")
    pen = Pen(c, fontkey, size)
    LAYOUTS[layout](pen, spec, qr_img)
    pen.footer()
    c.showPage()
    c.save()
    return buf.getvalue()
