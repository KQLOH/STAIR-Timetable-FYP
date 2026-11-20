"""Application-only reader selection from measured source structure, not identity."""
from io import BytesIO
from pathlib import Path
from backend.services.file_extractor import STAIRException
from backend.layout.block_capture import BlockCollector, detect_regions
from backend.layout.ruled_capture import capture, visible_grid
from backend.layout.ruled_models import RuledEvidence
from backend.layout.block_models import BlockEvidence, MAX_TOTAL_PIXELS
from backend.services.file_extractor import extract_document
from backend.services.extraction_adapter import adapt_extraction


class AutomaticCollector(BlockCollector):
    def __init__(self):
        super().__init__()
        self.selected = None
        self.page_count = 0
        self.total_pixels = 0

    def capture_page(self, source_id, image, page, adapter, data, runtime, regions, transform):
        self.total_pixels += image.width * image.height
        if self.total_pixels > MAX_TOTAL_PIXELS:
            raise ValueError('AUTO_TOTAL_PIXEL_CAP')
        found, _, _, _ = detect_regions(image, source_id, page)
        choice = 'ocr-block-grid-s2b-v1' if any(r.boundary_status == 'RECTANGULAR' for r in found) else (
            'ocr-ruled-grid-s2a-v1' if visible_grid(image)['status'] == 'MEASURED' else 'text')
        if self.page_count and choice != self.selected:
            raise ValueError('AUTO_MIXED_LAYOUTS_REQUIRE_SEPARATE_UPLOADS')
        self.selected = choice
        self.page_count += 1
        if choice == 'ocr-block-grid-s2b-v1':
            super().capture_page(source_id, image, page, adapter, data, runtime, regions, transform)
        elif choice == 'ocr-ruled-grid-s2a-v1':
            self.append(capture(source_id, image, page, adapter, data, runtime, regions, transform))


def extract_automatic(filename, media_type, content):
    collector = AutomaticCollector()
    force = False
    # Keep fully native PDFs native. Raster/mixed PDFs use one consistent OCR
    # coordinate/evidence route; never label OCR spans as native PDF evidence.
    is_pdf = Path(filename).suffix.lower() == '.pdf'
    if is_pdf and content.startswith(b'%PDF-'):
        import pdfplumber
        try:
            pdf = pdfplumber.open(BytesIO(content))
        except Exception:
            raise STAIRException('INVALID_PDF', 'The PDF cannot be opened safely. Try another file.', 422) from None
        with pdf:
            if len(pdf.pages) > collector.max_pages:
                raise ValueError('AUTO_PAGE_CAP')
            try:
                force = not all(page.extract_words() for page in pdf.pages)
            except Exception:
                raise STAIRException('INVALID_PDF', 'The PDF text cannot be read safely. Try another file.', 422) from None
    document = extract_document(filename, media_type, content, force_ocr=force,
                                collector=collector if force or not is_pdf else None)
    packet = adapt_extraction(document)
    profile = collector.selected if collector.selected not in {None, 'text'} else None
    sidecar = None
    if profile:
        model = BlockEvidence if profile == 'ocr-block-grid-s2b-v1' else RuledEvidence
        sidecar = model(source_document_id=document.source_id, content_sha256=document.checksum, pages=tuple(collector))
    return {'success': True, 'pipeline': 'evidence', 'data': packet.model_dump(mode='json'),
            'layout_profile': profile, 'layout_evidence': sidecar.model_dump(mode='json') if sidecar else None,
            'reader_selection': 'measured-structure-v1'}
