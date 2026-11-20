"""Interactive local owner consent; not imported by ordinary API startup."""
import argparse
from pathlib import Path
import shlex
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from backend.review.local_host import build_local_app, ORIGIN
    from backend.review.secure_directory import user_local_root
    from backend.review.models import ReviewError
    import uvicorn
    parser = argparse.ArgumentParser(description='STAIR Windows-logon, source-scoped student review host')
    parser.add_argument('--storage', type=Path,
        default=user_local_root() / 'STAIR' / 'StudentReview')
    parser.add_argument('--operating-point',type=Path,help='Private approved PRE-01/02/03 bootstrap manifest; never an HTTP registration')
    parser.add_argument('--operating-approval-sha256',help='Explicit trusted approval-artifact SHA-256, supplied by the operator')
    args = parser.parse_args()
    if bool(args.operating_point)!=bool(args.operating_approval_sha256):
        parser.error('Operating point and explicitly trusted approval hash are required together.')
    if not sys.stdin.isatty():
        raise SystemExit('An interactive Windows console is required; no automatic grant is issued.')
    registry=None
    if args.operating_point:
        from backend.review.registration import load_approved_registry
        try:registry=load_approved_registry(args.operating_point,args.operating_approval_sha256)
        except (ValueError,OSError,KeyError,TypeError):
            raise SystemExit('OPERATING_POINT_NOT_APPROVED_OR_FREEZE_INVALID; no ACCEPT host was started.') from None
    app, host = build_local_app(args.storage,registry=registry)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8020, access_log=False))
    worker = Thread(target=server.run, daemon=True); worker.start()
    print('STAIR local Windows-session review: ' + ORIGIN)
    print('Encrypted storage: ' + str(host.store.journal.directory))
    print('Actor is read from the Windows process logon SID, never a browser-supplied name.')
    print('Upload your timetable, request access, then personally authorize its original file here.')
    print('authorize <browser nonce> "C:\\path\\to\\your timetable.jpg"')
    print('Independent check: invite-check <check ID> "original file path"; the reviewer opens the returned link in Chrome Guest.')
    print('Then: authenticate-reviewer <nonce> (Windows password dialog). Final confirmation: confirm-reviewer <nonce>.')
    print('revoke-reviewers ends all independent access. No credential or approval is generated automatically.')
    print('This authorizes a 30-minute review session, not a correction or calendar approval. Type quit to stop.')
    try:
        while worker.is_alive():
            command = input('STAIR owner> ').strip()
            if command == 'quit':
                break
            try:
                parts = shlex.split(command, posix=False)
                if parts==['revoke-reviewers']:
                    host.independent.revoke_from_console();print('Independent reviewer access revoked.');continue
                if len(parts)==3 and parts[0]=='invite-check':
                    code=host.independent.invite_from_console(parts[1],parts[2].strip('"'))
                    print('Reviewer: open a separate Chrome Guest window, then '+ORIGIN+'/independent-review.html#'+code)
                    continue
                if len(parts)==2 and parts[0] in {'authenticate-reviewer','confirm-reviewer'}:
                    if parts[0]=='authenticate-reviewer':host.independent.authenticate_from_console(parts[1]);print('Distinct Windows reviewer authenticated. Click Connect reviewer in the reviewer window.')
                    else:host.independent.confirm_from_console(parts[1]);print('Explicit independent source verification recorded. Refresh the reviewer window.')
                    continue
                if len(parts) != 3 or parts[0] != 'authorize':
                    raise ValueError('Expected authorize <nonce> "original file path"')
                host.authorize_from_console(parts[1], parts[2].strip('"'))
                print('Exact source authorized for this Windows logon. Click Connect approved review in the browser.')
            except (ValueError, OSError, RuntimeError, ReviewError) as error:
                import re
                code=error.code if isinstance(error,ReviewError) else str(error)
                print('Request not completed. No automatic approval was issued.')
                if re.fullmatch('[A-Z][A-Z0-9_]{1,100}',code):print('Reason: '+code)
                print('Check the exact source, current inventory, unexpired request and reviewer account. Windows Hello/PIN-only or unavailable accounts need a supported Windows password; do not use the owner account instead.')
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        server.should_exit = True; worker.join(timeout=5)


if __name__ == '__main__':
    main()
