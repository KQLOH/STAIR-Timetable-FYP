"""Bounded original-PDF raster preview worker; no OCR, files or external calls.

Run in a separate process because PDFium rendering is not thread safe with
other PDF extraction requests. Only authenticated, hash-bound callers invoke it.
"""
from io import BytesIO
import sys


def render(raw,page):
    import pdfplumber
    if not raw.startswith(b'%PDF-') or not 0<len(raw)<=20*1024*1024:raise ValueError('Invalid PDF')
    with pdfplumber.open(BytesIO(raw)) as pdf:
        if not 1<=len(pdf.pages)<=100 or not 1<=page<=len(pdf.pages):raise ValueError('Invalid page')
        original=pdf.pages[page-1]
        edge=max(original.width,original.height)
        if edge<=0:raise ValueError('Invalid dimensions')
        image=original.to_image(resolution=min(144,2000*72/edge),force_mediabox=True).original
        output=BytesIO();image.save(output,format='PNG')
        if output.tell()>8*1024*1024:raise ValueError('Preview limit')
        return output.getvalue()


if __name__=='__main__':
    try:sys.stdout.buffer.write(render(sys.stdin.buffer.read(20*1024*1024+1),int(sys.argv[1])))
    except Exception:raise SystemExit(2) from None
