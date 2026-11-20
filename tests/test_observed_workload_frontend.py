"""Synthetic DOM tests; Chrome uploads are checked separately."""
from pathlib import Path
import shutil,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
class ObservedFrontendTests(unittest.TestCase):pass
def attach(case):
    def check(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/observed_workload_checks.cjs'),case],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
    setattr(ObservedFrontendTests,'test_'+case,check)
for case in ('unknown_scope','prefill_without_invention','invalid_dates_keep_timetable','current_saved_revision','late_reply_ignored','research_optional'):attach(case)
