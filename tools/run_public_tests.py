"""Public synthetic software inventory; no retained historical evidence required."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    if any((ROOT/name).exists() for name in ('tests/s4b_demo_host.py', 'tests/test_s4b_transport.py')):
        raise SystemExit('Use the public inventory; historical S4B tests stay in the original local project.')
    node = shutil.which('node')
    if not node:
        raise SystemExit('Node is required for the public frontend security checks.')
    (ROOT/'data/temp').mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    # Preserve unittest discover -s tests import semantics used by the existing
    # synthetic helpers; do not rewrite original imports to suit packaging.
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    start = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    javascript = subprocess.run([node, str(ROOT/'tests/s4b_transport_checks.js')], cwd=ROOT, capture_output=True, text=True, timeout=30)
    summary = {'python': {'run':result.testsRun, 'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
                         'failed':len(result.failures), 'errored':len(result.errors), 'skipped':len(result.skipped),
                         'expected_failures':len(result.expectedFailures), 'unexpected_successes':len(result.unexpectedSuccesses)},
               'node_transport_security_exit_code':javascript.returncode, 'elapsed_seconds':round(time.perf_counter()-start,3)}
    print(json.dumps(summary, sort_keys=True), flush=True)
    if javascript.returncode:
        print(javascript.stdout, javascript.stderr, file=sys.stderr)
    return 0 if result.wasSuccessful() and javascript.returncode==0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
