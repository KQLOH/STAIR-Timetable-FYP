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
    import uvicorn
    parser = argparse.ArgumentParser(description='STAIR Windows-logon, source-scoped student review host')
    parser.add_argument('--storage', type=Path,
        default=user_local_root() / 'STAIR' / 'StudentReview')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit('An interactive Windows console is required; no automatic grant is issued.')
    app, host = build_local_app(args.storage)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8020, access_log=False))
    worker = Thread(target=server.run, daemon=True); worker.start()
    print('STAIR local Windows-session review: ' + ORIGIN)
    print('Encrypted storage: ' + str(host.store.journal.directory))
    print('Actor is read from the Windows process logon SID, never a browser-supplied name.')
    print('Upload your timetable, request access, then personally authorize its original file here.')
    print('authorize <browser nonce> "C:\\path\\to\\your timetable.jpg"')
    print('This authorizes a 30-minute review session, not a correction or calendar approval. Type quit to stop.')
    try:
        while worker.is_alive():
            command = input('STAIR owner> ').strip()
            if command == 'quit':
                break
            try:
                parts = shlex.split(command, posix=False)
                if len(parts) != 3 or parts[0] != 'authorize':
                    raise ValueError('Expected authorize <nonce> "original file path"')
                host.authorize_from_console(parts[1], parts[2].strip('"'))
                print('Exact source authorized for this Windows logon. Click Connect approved review in the browser.')
            except (ValueError, OSError, RuntimeError):
                print('Authorization refused. Check the original file, source hash and unexpired browser request.')
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        server.should_exit = True; worker.join(timeout=5)


if __name__ == '__main__':
    main()
