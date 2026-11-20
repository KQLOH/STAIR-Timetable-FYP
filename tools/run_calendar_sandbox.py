"""Explicit local TEST_ONLY launcher; never installed in normal API startup."""
import argparse
from pathlib import Path
import sys
from threading import Thread
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description='STAIR Windows-owner TEST_ONLY calendar sandbox')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit('An interactive Windows owner console is required. No automatic grant is issued.')
    from backend.calendar.local_host import build_local_calendar, ORIGIN
    import uvicorn
    app, host = build_local_calendar()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8022, access_log=False))
    worker = Thread(target=server.run, daemon=True); worker.start()
    print('STAIR local TEST_ONLY calendar: ' + ORIGIN)
    print('Invented sample data only. Google Calendar and uploaded timetables are not connected.')
    print('Request access in the page, then personally type authorize-sandbox <nonce> here.')
    print('This grants access only; each exact preview needs your separate checkbox and approval.')
    print('History is local test data. Type quit to stop; restart requires fresh browser authorization.')
    try:
        while worker.is_alive():
            command = input('STAIR sandbox owner> ').strip()
            if command == 'quit':
                break
            try:
                parts = command.split()
                if len(parts) != 2 or parts[0] != 'authorize-sandbox':
                    raise ValueError()
                host.authorize_from_console(parts[1])
                print('Sandbox access authorized for this Windows owner. Connect in the page. No action approved or executed.')
            except (ValueError, OSError, RuntimeError):
                print('Authorization refused. Request fresh access in the local sandbox page.')
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        server.should_exit = True; worker.join(timeout=5)


if __name__ == '__main__':
    main()
