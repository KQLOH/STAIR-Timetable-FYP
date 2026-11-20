from pathlib import Path
import subprocess,unittest
class SourceRoutingFrontendTests(unittest.TestCase):
    def test_revision_binding_and_honest_source_routing_metrics(self):
        root=Path(__file__).resolve().parents[1]
        r=subprocess.run(['node',str(root/'tests/source_routing_checks.cjs')],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
