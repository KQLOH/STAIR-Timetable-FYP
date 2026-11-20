"""Synthetic UI preservation checks; actual Chrome coverage is recorded separately."""
from pathlib import Path
import shutil,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
class SourceDraftFrontendTests(unittest.TestCase):pass

def attach(case):
    def check(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/source_check_draft_checks.cjs'),case],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
    setattr(SourceDraftFrontendTests,'test_'+case,check)
for case in ('reopen_cancel','failed_save','reopen_late_edit','new_edits_during_save','temporal_inputs_preserved','dirty_analysis_blocked','connected_navigation','packet_key_order','revision_stale_preserved','wrong_scope_hidden','scope_restored','expired_access_hidden','load_source_change'):attach(case)
