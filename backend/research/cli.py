"""Reproducible read-only PRE-01 command; no evaluation, writers or auth calls."""

import argparse
import json
from pathlib import Path
import sys

from pydantic import ValidationError

from backend.research.baseline_reader import load_baseline_metadata
from backend.research.models import ResearchBundle
from backend.research.readiness import assess_pre01


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M2 PRE-01 readiness inspection (read-only)")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("pre01")
    choice = command.add_mutually_exclusive_group(required=True)
    choice.add_argument("--baseline-root", type=Path, help="Read the fixed metadata allowlist; no labels or predictions")
    choice.add_argument("--bundle", type=Path, help="Approved preparation bundle; never a prediction output")
    command.add_argument("--evidence-root", type=Path, help="Required with --bundle; files are hash checked inside this root")
    args = parser.parse_args(argv)
    try:
        if args.baseline_root:
            root = args.baseline_root
            bundle, observation = load_baseline_metadata(root)
        else:
            if args.evidence_root is None:
                parser.error("--evidence-root is required with --bundle")
            root = args.evidence_root
            bundle = ResearchBundle.model_validate_json(args.bundle.read_text(encoding="utf-8"))
            observation = {"mode": "PREPARATION_BUNDLE_EVIDENCE_CHECK"}
        report = assess_pre01(bundle, root)
        result = {"report": report.model_dump(mode="json"), "observation": observation}
        # Keep inconsistent legacy gold claims explicit; never use them as proof.
        claims = observation.get("legacy_claims_not_verification", {})
        if claims.get("legacy_claimed_verified_annotations") or "confirmed" in claims.get("legacy_annotation_statuses", []):
            result["report"]["status"] = "NOT READY"
            result["report"]["reason_codes"] = sorted(set(result["report"]["reason_codes"]) | {"LEGACY_GOLD_CLAIMS_REQUIRE_RECONCILIATION"})
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0 if result["report"]["status"] == "READY" else 1
    except (OSError, ValueError, ValidationError, KeyError, TypeError):
        # Do not leak raw source content through validation errors.
        print(json.dumps({"report": {"status": "NOT READY", "reason_codes": ["INVALID_OR_INACCESSIBLE_PREPARATION_INPUT"]}}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
