"""Readable, deterministic PDF of the complete immutable approved packet."""
import io
import re
from html import escape
from pathlib import Path

from .delivery import packet_text


def packet_pdf(snapshot):
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak

    fonts = Path(reportlab.__file__).parent / 'fonts'
    for name, file in [('Packet', 'Vera.ttf'), ('PacketBold', 'VeraBd.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    body = ParagraphStyle('body', fontName='Packet', fontSize=9, leading=14,
                          textColor=colors.HexColor('#273340'), spaceAfter=6,
                          alignment=TA_LEFT, splitLongWords=True)
    heading = ParagraphStyle('heading', parent=body, fontName='PacketBold',
                             fontSize=12, leading=17, spaceBefore=13, keepWithNext=True)
    title = ParagraphStyle('title', parent=heading, fontSize=21, leading=27, spaceBefore=0)
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=45, leftMargin=45,
                            topMargin=48, bottomMargin=48, title='Pinpoint location and route',
                            author='Pinpoint')
    story = []
    for line in packet_text(snapshot).splitlines():
        if not line.strip():
            continue
        if line == '## Reviewed agent packet':
            story.append(PageBreak())
        level = len(line) - len(line.lstrip('#'))
        text = line.lstrip('#').strip() if level else line
        # Keep content, provenance and unknowns; format Markdown rather than execute it.
        text = text.replace('—', '-').replace('–', '-').replace('→', ' > ')
        text = re.sub(r'!\[([^]]*)\]\(([^)]+)\)', r'\1: \2', text)
        text = escape(text)
        text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
        story.append(Paragraph(text, title if level == 1 and not story else heading if level else body))
    def footer(canvas, document):
        canvas.setFont('Packet', 8)
        canvas.setFillColor(colors.HexColor('#657383'))
        canvas.drawString(45, 26, f'Pinpoint | Approved revision {snapshot["incident"]["revision"]}')
        canvas.drawRightString(A4[0] - 45, 26, f'Page {document.page}')
    def deterministic_canvas(*args, **kwargs):
        return Canvas(*args, **dict(kwargs, invariant=1))
    doc.build(story, onFirstPage=footer, onLaterPages=footer, canvasmaker=deterministic_canvas)
    return stream.getvalue()
