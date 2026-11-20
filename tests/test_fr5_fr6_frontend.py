"""Synthetic frontend behavior tests; actual Chrome is checked separately."""
from pathlib import Path
import shutil,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
class RoutingCalendarFrontendTests(unittest.TestCase):pass
def attach(case):
    def check(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/fr5_fr6_integration_checks.cjs'),case],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
    setattr(RoutingCalendarFrontendTests,'test_'+case,check)
for case in ('routing_projection','stale_projection','accept_projection_refused','current_population','replay_confirmed','replay_unknown','replay_failed','replay_preview_required'):attach(case)
