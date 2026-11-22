const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'frontend/js/student-account.js'),'utf8');
const raw='{"source_packet":{"source":"synthetic"},"reconstruction":{"canonical_output":{"score":30.0,"penalty":0.0}}}';
assert.notEqual(JSON.stringify(JSON.parse(raw)),raw,'Fixture must expose the actual number re-encoding defect.');
const nodes=new Map(),sent=[];const get=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',className:''});return nodes.get(id)};
const capture={source_document_id:'synthetic:serialization',content_sha256:'0'.repeat(64)};
const sandbox={console,URL,Headers,Request,queueMicrotask,location:{href:'http://synthetic.invalid/'},
 document:{body:{classList:{add(){}}},getElementById:get,readyState:'loading',addEventListener(){}},
 reviewSource:()=>({packet:{source:capture},result:{reconstruction:{}}}),state:{upload:null},
 reviewController:{refreshQueue:async()=>{},restoreSavedSource:async()=>{}},
 STAIRUpload:{reviewIngestBody:()=>raw},
 fetch:async(url,options={})=>{sent.push({url,body:options.body});return {ok:true,status:200,json:async()=>({task_ids:[]})}}};
sandbox.window=sandbox;
const probe="window.__qa={connect:connectSource,api,setup(){user={username:'synthetic'};refresh=async()=>{workspace={tasks:[]}};}};\n    ";
vm.runInNewContext(source.replace('window.STAIRAccount={sourceChanged()',probe+'window.STAIRAccount={sourceChanged()'),sandbox);
(async()=>{
 sandbox.__qa.setup();await sandbox.__qa.connect();
 const request=sent.find(r=>r.url==='/api/review/ingest');assert(request,'Actual automatic source-connect path must reach ingest.');
 assert.equal(request.body,raw,'Preserve original number representation and the complete bound reconstruction envelope.');
 await sandbox.__qa.api('/api/student/schedule',{synthetic:true});assert.equal(sent.at(-1).body,'{"synthetic":true}');
 console.log('PASS actual account source-connect preserves bound JSON; ordinary form bodies remain encoded.');
})().catch(error=>{console.error(error);process.exitCode=1});
