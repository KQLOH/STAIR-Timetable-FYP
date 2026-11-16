"""Manual reader UI and backend-profile compatibility; synthetic inputs only."""
from pathlib import Path
import subprocess
import unittest
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
class ManualReaderTests(unittest.TestCase):
    def setUp(self):
        self.page=BeautifulSoup((ROOT/'frontend/index.html').read_text(encoding='utf-8'),'html.parser')
    def test_default_is_manual_general(self):
        reader=self.page.find(id='timetableReader')
        self.assertEqual(reader.find('option',selected=True)['value'],'general')
        self.assertEqual([o['value'] for o in reader.find_all('option')],['general','colored','grid'])
        self.assertIsNone(reader.find_parent('details'))
        self.assertFalse(self.page.find(id='automaticReader').has_attr('checked'))
        self.assertFalse(self.page.find(id='useAdvancedReader').has_attr('checked'))
    def test_rt4_enabled_and_technical_settings_disclosed(self):
        self.assertTrue(self.page.find(id='runContextCorrection').has_attr('checked'))
        for name in ('automaticReader','useAdvancedReader','runContextCorrection','runBlockGridS2b','runRuledGridS2a','runLayoutAssociation','runP1','extractionPipeline'):
            self.assertEqual(self.page.find(id=name).find_parent('details')['id'],'readerAdvanced')
    def test_normal_reader_names_are_understandable(self):
        text=self.page.find(id='timetableReader').get_text(' ',strip=True)
        for name in ('Colored timetable','Grid timetable','Standard image or PDF'):self.assertIn(name,text)
        self.assertNotIn('evidence',text);self.assertNotIn('P1',text);self.assertNotIn('M5.2',text)
    def test_friendly_edit_and_safeguards_remain(self):
        self.assertIsNotNone(self.page.find(id='studentSave'))
        self.assertIsNotNone(self.page.find(id='studentConfirmed'))
        self.assertIsNotNone(self.page.find(id='sourceInspection'))
        self.assertTrue(self.page.find(id='syncButton').has_attr('disabled'))
    def test_actual_reader_configuration(self):
        result=subprocess.run(['node',str(ROOT/'tests/manual_reader_checks.cjs')],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
