const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../frontend/js/student-account.js'),'utf8');
function fixture(){
 let reply;const nodes=new Map();
 const environment={URL,Headers,Request,URLSearchParams,console,queueMicrotask,
  location:{href:'http://synthetic.invalid/',origin:'http://synthetic.invalid',search:''},
  document:{body:{classList:{add(){}}},readyState:'loading',addEventListener(){},getElementById(id){if(!nodes.has(id))nodes.set(id,{});return nodes.get(id)}},
  fetch:()=>new Promise(resolve=>{reply=resolve})};
 environment.window=environment;environment.__clears=0;
 const probe="clearSession=()=>{window.__clears++;generation++;user=null;csrf='';};window.__qa={assign(name,version){user={username:name};generation=version;csrf='synthetic-'+version;},current(){return user?.username;}};\n";
 vm.runInNewContext(source.replace('window.STAIRAccount={sourceChanged()',probe+'window.STAIRAccount={sourceChanged()'),environment);
 return {environment,reply:response=>reply(response)};
}
(async()=>{
 for(const status of [401,200]){
  const f=fixture(),e=f.environment;e.__qa.assign('synthetic-old',0);
  const pending=e.fetch('/api/student/workspace');e.__qa.assign('synthetic-new',1);
  f.reply({status,ok:status===200,json:async()=>({private:'synthetic-old-account-only'})});
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert.equal(e.__clears,0);assert.equal(e.__qa.current(),'synthetic-new');
 }
 const f=fixture(),e=f.environment;e.__qa.assign('synthetic-current',0);
 const pending=e.fetch('/api/student/workspace');f.reply({status:401,ok:false});
 assert.equal((await pending).status,401);assert.equal(e.__clears,1);assert.equal(e.__qa.current(),undefined);
 const fields=new Map(),shell={inert:false};
 const field=id=>{if(!fields.has(id))fields.set(id,{value:'',checked:true,open:false,replaceChildren(){},focus(){},showModal(){this.open=true},querySelectorAll(){return []}});return fields.get(id)};
 const isolated={URL,Headers,Request,URLSearchParams,console,queueMicrotask,
  location:{href:'http://synthetic.invalid/',origin:'http://synthetic.invalid',search:''},
  document:{body:{classList:{add(){}}},readyState:'loading',addEventListener(){},getElementById:field,querySelector:()=>shell},
  state:{},reviewController:{forget(){}},uploadController:{cancel(){}},clearUploadView(){},renderEverything(){},fetch:async()=>({ok:true,status:200})};
 isolated.window=isolated;
 vm.runInNewContext(source.replace('window.STAIRAccount={sourceChanged()',"window.__qaClear=clearSession;window.STAIRAccount={sourceChanged()"),isolated);
 field('copiedTimetableText').value='SYNTHETIC PRIVATE PREVIOUS SOURCE';field('studentBatchNote').value='SYNTHETIC PRIVATE NOTE';
 isolated.__qaClear();
 assert.equal(field('copiedTimetableText').value,'','Ended sessions must not expose old timetable input to another account.');
 assert.equal(field('studentBatchNote').value,'');assert.equal(field('studentBatchConfirmed').checked,false);
 console.log('PASS stale session responses neither clear current access nor reveal old data; current expiry still clears access.');
})().catch(error=>{console.error(error);process.exitCode=1});
