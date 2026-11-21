"""Start the account-based application. No interactive console authorization."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from backend.review.secure_directory import user_local_root
    from backend.student.api import build_student_app
    import uvicorn
    parser = argparse.ArgumentParser(description='STAIR user-friendly student application')
    parser.add_argument('--port', type=int, default=8020)
    parser.add_argument('--storage', type=Path, default=user_local_root() / 'STAIR' / 'StudentAccounts')
    parser.add_argument('--open-browser', action='store_true', help='Open the app once its server is ready')
    parser.add_argument('--operating-point', type=Path)
    parser.add_argument('--operating-approval-sha256')
    parser.add_argument('--calendar-context', type=Path)
    parser.add_argument('--calendar-context-approval-sha256')
    parser.add_argument('--analysis-provider', type=Path)
    parser.add_argument('--analysis-provider-approval-sha256')
    parser.add_argument('--google-credentials', type=Path, help='Google Web OAuth JSON outside the repository')
    parser.add_argument('--google-calendar-id', help='Dedicated secondary test calendar ID')
    parser.add_argument('--check-google-config', action='store_true', help='Validate configuration offline, then exit')
    parser.add_argument('--calendar-policy', choices=('human-confirmed', 'registered-accept'),
                        default='human-confirmed',
                        help='Strict research output requires authentic ACCEPT and source-backed dates')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use a port between 1024 and 65535.')
    origin = 'http://127.0.0.1:' + str(args.port)
    google_config = None
    if bool(args.google_credentials) != bool(args.google_calendar_id):
        parser.error('Supply --google-credentials and --google-calendar-id together.')
    if args.google_credentials:
        from backend.student.google_config import load_google_config
        try: google_config = load_google_config(args.google_credentials, args.google_calendar_id, origin)
        except (OSError, ValueError, TypeError):
            raise SystemExit('GOOGLE_SETUP_INVALID: use a private Web OAuth file, the exact callback URL and a secondary calendar ID. See /google-calendar-setup.') from None
    if args.check_google_config:
        if google_config is None: parser.error('Offline validation requires both Google configuration arguments.')
        print('Google configuration passed offline checks. No login, API call or calendar write was performed.')
        print('Required Web OAuth callback: '+origin+'/api/student/google/callback')
        return
    if bool(args.operating_point) != bool(args.operating_approval_sha256):
        parser.error('Supply the approved operating point and its trusted approval hash together.')
    if bool(args.calendar_context) != bool(args.calendar_context_approval_sha256):
        parser.error('Supply the approved calendar context and its trusted approval hash together.')
    context = None
    if args.calendar_context:
        from backend.student.calendar_context import ApprovedContextFile
        try: context = ApprovedContextFile(args.calendar_context, args.calendar_context_approval_sha256)
        except (OSError, ValueError, TypeError):
            raise SystemExit('CALENDAR_CONTEXT_NOT_APPROVED_OR_INVALID; no server was started.') from None
    if bool(args.analysis_provider) != bool(args.analysis_provider_approval_sha256):
        parser.error('Supply the approved analysis-provider file and its trusted approval hash together.')
    grounded = None
    if args.analysis_provider:
        from backend.analysis.provider import CompatibleProvider
        from backend.analysis.grounded import GroundedAnalyser
        try: grounded = GroundedAnalyser(CompatibleProvider(args.analysis_provider, args.analysis_provider_approval_sha256))
        except (OSError, ValueError, TypeError):
            raise SystemExit('ANALYSIS_PROVIDER_NOT_APPROVED_OR_INVALID; no server was started.') from None
    registry = None
    if args.operating_point:
        from backend.review.registration import load_approved_registry
        registry = load_approved_registry(args.operating_point, args.operating_approval_sha256)
    app = build_student_app(args.storage, origin=origin, registry=registry,
                            calendar_policy=args.calendar_policy, calendar_context=context, grounded_service=grounded,
                            google_config=google_config)
    print('STAIR Student App: ' + origin)
    print('Create your STAIR account in the browser. Google and Intranet sign-in are separate.')
    print('Calendar policy: ' + args.calendar_policy)
    if args.calendar_policy == 'registered-accept' and registry is None:
        print('Strict output is blocked until an authentic approved operating point is supplied.')
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, access_log=False, workers=1))
    if args.open_browser:
        import threading
        import time
        import webbrowser
        def open_when_ready():
            for _ in range(300):
                if server.started:
                    webbrowser.open(origin)
                    return
                if server.should_exit:
                    return
                time.sleep(0.1)
        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()


if __name__ == '__main__':
    main()
