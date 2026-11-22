"""Raster PDF route reflects original extraction evidence; baselines stay frozen."""
import unittest
from backend.models.extraction import DocumentEvidence,EvidencePage,EvidenceRegion,BoundingBox
from backend.services.extraction_adapter import adapt_extraction,adapt_baseline

class PDFEvidenceRouteTests(unittest.TestCase):
    def document(self,adapters):
        return DocumentEvidence(source_id='synthetic-pdf',route_type='upload_pdf',original_label='synthetic.pdf',
            media_type='application/pdf',checksum='a'*64,parser_version='synthetic',pages=[
            EvidencePage(page=i,width=300,height=300,text='Invented class',regions=[EvidenceRegion(
                evidence_id='synthetic-'+str(i),page=i,line=1,text='Invented class',adapter=adapter,
                bbox=BoundingBox(x0=1,y0=1,x1=100,y1=20))]) for i,adapter in enumerate(adapters,1)])
    def test_raster_pdf_selects_ocr_without_changing_source_or_baseline(self):
        raw=self.document(['pdf-raster-tesseract-1.0']);packet=adapt_extraction(raw)
        self.assertEqual(packet.source.route,'ocr')
        self.assertEqual(packet.source.content_sha256,raw.checksum)
        self.assertEqual(packet.source.media_type,'application/pdf')
        self.assertEqual(packet.evidence[0].bbox.coordinate_space,'PIXELS')
        with self.assertRaises(ValueError):adapt_baseline(raw,'B2')
    def test_native_pdf_retains_native_route(self):
        packet=adapt_extraction(self.document(['pdf-native-coordinate']))
        self.assertEqual(packet.source.route,'native_pdf')
        self.assertEqual(packet.evidence[0].bbox.coordinate_space,'PDF_POINTS')
    def test_mixed_pdf_preserves_distinct_page_evidence(self):
        packet=adapt_extraction(self.document(['pdf-native-coordinate','pdf-raster-tesseract-1.0']))
        self.assertEqual(packet.source.route,'native_pdf')
        self.assertEqual([s.route for s in packet.evidence],['native_pdf','ocr'])
