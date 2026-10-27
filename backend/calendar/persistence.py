"""Append-only hash-linked snapshots published atomically; no database migration.

OS file locks serialize writers across processes. Staging files are retained,
never interpreted as committed state. Corrupt committed records fail closed.
"""
from contextlib import contextmanager
import json,os
from pathlib import Path
from uuid import uuid4
from backend.calendar.models import CalendarError
from backend.reconstruction.models import canonical_bytes,digest
from backend.review.auth import utc_now

ROOT=Path(__file__).resolve().parents[2]


class Journal:
    def __init__(self,directory,*,test_only=False):
        self.directory=Path(directory).resolve()
        allowed=ROOT/'data/temp' if test_only else ROOT/'data/calendar_sandbox'
        if not self.directory.is_relative_to(allowed.resolve()) or self.directory==allowed.resolve():
            raise CalendarError('SANDBOX_PATH_NOT_ISOLATED',422)
        if test_only and not self.directory.relative_to(allowed.resolve()).parts[0].startswith('m6-test-'):
            raise CalendarError('SYNTHETIC_SANDBOX_PATH_REQUIRED',422)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.records=self.directory/'records'
        if not self.records.resolve().is_relative_to(self.directory): raise CalendarError('SANDBOX_PATH_NOT_ISOLATED',422)
        self.records.mkdir(exist_ok=True)

    @contextmanager
    def locked(self):
        if not (self.directory/'writer.lock').resolve().is_relative_to(self.directory):
            raise CalendarError('SANDBOX_PATH_NOT_ISOLATED',422)
        with (self.directory/'writer.lock').open('a+b') as handle:
            if handle.tell()==0: handle.write(b'0');handle.flush()
            handle.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: raise CalendarError('SANDBOX_BUSY',409) from None
            try: yield self
            finally:
                handle.seek(0)
                if os.name=='nt': msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else: fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

    def load(self):
        previous=None;state=None;history=[]
        try:
            for index,path in enumerate(sorted(self.records.glob('*.json')),1):
                record=json.loads(path.read_text(encoding='utf-8'))
                claimed=record.pop('record_hash')
                if record['sequence']!=index or record['previous_hash']!=previous or digest(record)!=claimed or path.name!=f'{index:08d}-{claimed}.json':
                    raise ValueError('Journal integrity')
                previous=claimed;state=record['state'];history.append({**record,'record_hash':claimed})
        except (OSError,ValueError,KeyError,TypeError): raise CalendarError('SANDBOX_JOURNAL_CORRUPT',409) from None
        return state,history

    def append(self,state,operation,actor,details=None):
        _,history=self.load()
        record={'sequence':len(history)+1,'previous_hash':history[-1]['record_hash'] if history else None,
                'operation':operation,'actor_id':actor,'recorded_at':utc_now().isoformat(),'details':details or {},'state':state}
        value=digest(record);record['record_hash']=value
        stage=self.directory/(uuid4().hex+'.staging')
        with stage.open('xb') as handle:
            handle.write(canonical_bytes(record));handle.flush();os.fsync(handle.fileno())
        # Hard-link creation cannot overwrite a committed record. Crash recovery
        # reads published records; orphan staging files are retained as diagnostics.
        target=self.records/f'{record["sequence"]:08d}-{value}.json'
        os.link(stage,target)
        return record
