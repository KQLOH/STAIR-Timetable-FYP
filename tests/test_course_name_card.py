"""Name-only course cards must retain a valid canonical course identity."""
import subprocess
from pathlib import Path
import unittest
class CourseNameCardTests(unittest.TestCase):
    def test_name_only_course_card(self):
        root=Path(__file__).resolve().parents[1]
        code="""require('./frontend/js/upload.js');
const event={candidate_id:'synthetic',revision:{content:{course_name:'Invented Science',session_type:'Lecture',start_time:'09:00',end_time:'10:00',location:'X1',day_of_week:1},observations:[]}};
const view=global.STAIRUpload.eventView(event,'synthetic');
if(view.subject!=='Invented Science'||!view.completeCandidate)throw Error('Name-only identity lost');
event.revision.content.course_name=null;
if(global.STAIRUpload.eventView(event,'synthetic').completeCandidate)throw Error('Missing identity accepted');"""
        subprocess.run(['node','-e',code],cwd=root,check=True,capture_output=True,text=True)
