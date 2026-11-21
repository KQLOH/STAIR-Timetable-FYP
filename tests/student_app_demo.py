"""In-memory invented timetable for browser QA; never a real account host."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_student_accounts import test_accounts
from test_m5_policy import fixture
from backend.student.api import create_student_app

accounts = test_accounts()
_, session = accounts.sign_in('synthetic_demo', 'invented-demo-password', create=True)
request, _ = fixture('CS101 Monday 2026-11-23 09:00-11:00 Lecture Room: A1',
                     'CS102 Tuesday 2026-11-24 13:00-14:00 Tutorial Room: A2')
accounts.enroll_source(session, request.source_packet.source)
accounts.store(session).ingest(request, session.principal)
app = create_student_app(accounts, origin='http://127.0.0.1:8031')

if __name__ == '__main__':
    import uvicorn
    if '--calendar-preview' in sys.argv:
        # Display-only native-browser QA, with no login dialog or real-user data.
        # The twelve stored occurrences are created through the actual approval
        # workflow using explicitly invented fixtures, never a research Gold set.
        import json
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, Response
        from backend.student import workflow
        store = accounts.store(session)
        tasks = store.queue_summary(session.principal)['tasks']
        batch = workflow.BatchSchedule(classes=tuple(workflow.BatchClass(task_id=t['task_id'],
            revision_id=t['current_event']['revision']['revision_id']) for t in tasks),
            frequency='WEEKLY', starts_on='2026-11-23', ends_on='2026-12-31',
            timezone='Asia/Kuala_Lumpur', checked_original=True, source_note='SYNTHETIC browser QA only')
        prepared = workflow.save_batch(accounts, session, batch)
        plan = workflow.local_preview(accounts, session, prepared['ready_task_ids'])
        workflow.approve(accounts, session, plan['id'])
        workflow.execute(accounts, session, plan['id'])
        with accounts.transaction() as state:
            entries = state['workspaces'][session.user_id]['entries']
        metadata = {op['key']: {'task_id': op['task_id'], 'revision_id': op['revision_id'],
                              'current_revision_id': op['revision_id']} for op in plan['operations']}
        preview_app = FastAPI()

        @preview_app.get('/qa-calendar')
        def preview_page():
            return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>STAIR sandbox calendar — synthetic QA</title>'
                '<link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/student.css">'
                '<link rel="stylesheet" href="/sandbox-calendar.css"></head>'
                '<body><main style="padding:24px;max-width:1450px;margin:auto">'
                '<p>Synthetic display-only QA. These are invented classes, not your timetable.</p>'
                '<div class="student-actions"><button class="button secondary" id="qaPreview">Show synthetic pending update</button>'
                '<button class="button secondary" id="qaClear">Clear synthetic preview</button></div>'
                '<section id="qaCalendar"></section></main><script src="/js/sandbox-calendar.js"></script>'
                '<script src="/qa-calendar-init.js"></script></body></html>')

        @preview_app.get('/qa-calendar-init.js')
        def preview_script():
            payload = json.dumps({'entries': entries, 'metadata': metadata})
            script = 'const data=' + payload + ';\n'
            script += "const calendar=new STAIRSavedCalendar.Calendar(document.getElementById('qaCalendar'),{onReview:async()=>{throw Error('Display-only QA. Source review belongs to the signed-in student app.');}});calendar.update(data.entries,data.metadata);\n"
            script += "document.getElementById('qaPreview').onclick=()=>{const key=Object.keys(data.entries)[0];calendar.setPreview({id:'synthetic-preview',target:'local',expires:Date.now()/1000+600,operations:[{key,action:'UPDATE',body:{...data.entries[key],location:'Invented pending room'},task_id:'synthetic-task',revision_id:'synthetic-revision'}]});};\n"
            script += "document.getElementById('qaClear').onclick=()=>calendar.setPreview(null);"
            return Response(script, media_type='application/javascript')

        preview_app.mount('/', app)
        app = preview_app
    uvicorn.run(app, host='127.0.0.1', port=8031, access_log=False)
