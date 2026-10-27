/* M7.1 regression fixtures are synthetic; no portal, storage or calendar writes. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),which=process.argv[3];
class Element {
    constructor(){this.children=[];this.dataset={};this.style={};this.listeners={};this.value='';this.textContent='';this.innerHTML='';this.classList={toggle(){},add(){},remove(){},contains(){return false;}};}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=[...nodes];this.innerHTML='';this.textContent='';}
    addEventListener(name,fn){this.listeners[name]=fn;}
    setAttribute(){}
    querySelectorAll(){return [];}
}
const nodes=new Map(),windowHandlers={};
global.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},
    createElement:()=>new Element(),querySelector:()=>null,querySelectorAll:()=>[],addEventListener:(name,fn)=>{windowHandlers[name]=fn;}};
global.window=global;global.addEventListener=(name,fn)=>{windowHandlers[name]=fn;};
const response=data=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>data,text:async()=>JSON.stringify(data)});
global.fetch=async()=>{throw Error('Unexpected request');};
for(const file of ['upload.js','intranet.js','analysis.js','app.js'])vm.runInThisContext(fs.readFileSync('frontend/js/'+file,'utf8'),{filename:file});
vm.runInThisContext('renderMetrics=()=>{};renderReview=()=>{};renderCalendar=()=>{};showToast=()=>{};markPipelineComplete=()=>{};showLoading=()=>{};closeIntranetLogin=()=>{};');
const get=id=>document.getElementById(id),stateView=()=>vm.runInThisContext('state');
const upload={filename:'synthetic.png',size:10,pipeline:'evidence',busy:true,status:'Processing',stages:{},diagnostics:[],extraction:null,reconstruction:null};
const structured=fixture.structured;
const source=structured.canonical;
const echo=()=>vm.runInThisContext('uploadController').notify({...upload});
function loginFields(){get('intranetStudentId').value='SYNTHETIC-ID';get('intranetPassword').value='SYNTHETIC-PASSWORD';}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function scope(){for(const [id,value] of Object.entries({analysisStart:'2026-01-05',analysisEnd:'2026-01-11',analysisZone:'Etc/UTC',analysisScope:'synthetic'}))get(id).value=value;}
const output={version:'m7-development-v1',completeness:{label:'OBSERVED EVENTS ONLY',reason_codes:['TEST_BLOCKER']},facts:[],recommendations:[],recommendation_status:'BLOCKED_INCOMPLETE_SCHEDULE',registered_p1a_status:'NOT_EXECUTED',registered_blockers:[]};
const cases={
    async pending_upload_cannot_reuse_tar(){assert.equal(STAIRAnalysis.packetFrom({...upload},source),null);},
    async upload_clears_structured_packet(){stateView().canonical=source;echo();assert.equal(stateView().canonical,null);},
    async source_switch_silent_reset(){
        const controller=vm.runInThisContext('uploadController');controller.result={...upload};let notices=0;controller.notify=()=>notices++;
        vm.runInThisContext('clearUploadView()');assert.equal(notices,0);
    },
    async late_refresh_cannot_replace_sample(){
        const d=deferred();global.STAIRIntranet={...STAIRIntranet,sessionRequest:()=>d.promise};stateView().connectionToken='synthetic-token';
        const pending=vm.runInThisContext('refreshIntranet()');vm.runInThisContext('loadSampleData()');d.resolve({success:true,data:structured});await pending;
        assert.equal(stateView().source,'sample');assert.equal(stateView().events[0].id,'sample-1');
    },
    async late_refresh_cannot_replace_upload(){
        const d=deferred();global.STAIRIntranet={...STAIRIntranet,sessionRequest:()=>d.promise};stateView().connectionToken='synthetic-token';
        const pending=vm.runInThisContext('refreshIntranet()');vm.runInThisContext('uploadController.run=async()=>{};previewUploadedFile({name:"synthetic.png"})');echo();
        d.resolve({success:true,data:structured});await pending;assert.equal(stateView().source,'upload');
    },
    async connect_while_uploading_keeps_structured_label(){
        vm.runInThisContext('uploadController.result=global.syntheticUpload');
    },
    async late_connect_cannot_replace_sample(){
        loginFields();const d=deferred(),closed=[];global.fetch=()=>d.promise;
        global.STAIRIntranet={...STAIRIntranet,sessionRequest:async(base,path,token)=>{closed.push({path,token});return {success:true};}};
        const pending=vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');vm.runInThisContext('loadSampleData()');
        d.resolve(response({success:true,connection_token:'synthetic-late-token',student_name:'SYNTHETIC',data:structured}));await pending;
        assert.equal(stateView().source,'sample');assert(closed.some(c=>c.path==='/api/intranet/disconnect'&&c.token==='synthetic-late-token'));
        assert.equal(get('intranetPassword').value,'');
    },
    async sample_dates_match_weekdays(){
        vm.runInThisContext('loadSampleData()');const days=['sunday','monday','tuesday','wednesday','thursday','friday','saturday'];
        for(const e of stateView().events)assert.equal(days[new Date(e.date+'T12:00:00Z').getUTCDay()],e.day);
    },
    async sample_completeness_is_field_presence(){
        vm.runInThisContext('loadSampleData()');assert(stateView().events.every(e=>e.completeCandidate===true));
    },
    async pagehide_invalidates_analysis(){
        scope();const d=deferred();let signal;global.fetch=(url,options)=>{signal=options.signal;return d.promise;};
        stateView().canonical=source;const pending=vm.runInThisContext('analysisController.run()');windowHandlers.pagehide();
        try {assert(signal.aborted);} finally {d.resolve(response(output));await pending;}
    },
    async sample_preview_replaces_old_source(){
        get('documentPreview').innerHTML='OLD_SOURCE_PREVIEW';vm.runInThisContext('loadSampleData()');
        assert(!get('documentPreview').innerHTML.includes('OLD_SOURCE_PREVIEW'));assert(get('documentName').textContent.includes('Synthetic'));
    },
    async forced_analysis_invalidation(){
        scope();const d=deferred();let signal;global.fetch=(url,options)=>{signal=options.signal;return d.promise;};
        const c=new STAIRAnalysis.Controller({base:'http://synthetic',getSource:()=>({canonical:source})});const pending=c.run();c.invalidate(null,true);
        try {assert(signal.aborted);} finally {d.resolve(response(output));await pending;}
        assert.equal(get('analysisResult').children[0].textContent.startsWith('NOT ASSESSED'),true);
    },
    async login_failure_clears_password(){
        loginFields();global.fetch=async()=>{throw Error('synthetic connection failure');};await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');assert.equal(get('intranetPassword').value,'');
    },
    async expired_refresh_clears_controls_preserves_events(){
        const s=stateView();s.connectionToken='synthetic-old';s.events=[{id:'retained-unassessed'}];
        global.fetch=async()=>({...response({detail:'PRIVATE_CANARY'}),ok:false,status:401});
        await vm.runInThisContext('refreshIntranet()');assert.equal(s.connectionToken,null);
        assert.equal(s.events[0].id,'retained-unassessed');assert.equal(get('refreshIntranet').disabled,true);
        assert.equal(get('disconnectIntranet').disabled,true);assert.match(get('processingStatus').textContent,/expired.*unassessed/);
    },
    async expired_disconnect_clears_controls(){
        stateView().connectionToken='synthetic-old';global.fetch=async()=>({...response({detail:'PRIVATE_CANARY'}),ok:false,status:401});
        await vm.runInThisContext('disconnectIntranet()');assert.equal(stateView().connectionToken,null);
        assert.equal(get('disconnectIntranet').disabled,true);assert.match(get('processingStatus').textContent,/expired/);
    },
    async late_expired_refresh_preserves_new_session(){
        const d=deferred(),s=stateView();s.connectionToken='synthetic-old';global.fetch=()=>d.promise;
        const pending=vm.runInThisContext('refreshIntranet()');s.connectionToken='synthetic-new';
        get('processingStatus').textContent='NEW_SOURCE';
        d.resolve({...response({detail:'PRIVATE_CANARY'}),ok:false,status:401});await pending;
        assert.equal(s.connectionToken,'synthetic-new');assert.equal(get('processingStatus').textContent,'NEW_SOURCE');
    },
    async late_expired_disconnect_preserves_new_session(){
        const d=deferred(),s=stateView();s.connectionToken='synthetic-old';global.fetch=()=>d.promise;
        const pending=vm.runInThisContext('disconnectIntranet()');s.connectionToken='synthetic-new';
        get('processingStatus').textContent='NEW_SOURCE';
        d.resolve({...response({detail:'PRIVATE_CANARY'}),ok:false,status:401});await pending;
        assert.equal(s.connectionToken,'synthetic-new');assert.equal(get('processingStatus').textContent,'NEW_SOURCE');
    },
    async refresh_server_error_retains_unconfirmed_connection(){
        stateView().connectionToken='synthetic-current';global.fetch=async()=>({...response({detail:'PRIVATE_CANARY'}),ok:false,status:502});
        await vm.runInThisContext('refreshIntranet()');assert.equal(stateView().connectionToken,'synthetic-current');
    },
    async session_error_redacts_payload_preserves_http_status(){
        global.fetch=async()=>({...response({detail:'PRIVATE_CANARY'}),ok:false,status:401});
        await assert.rejects(STAIRIntranet.sessionRequest('http://synthetic','/api/intranet/timetable','synthetic-token'),
            error=>error.status===401&&!error.message.includes('PRIVATE_CANARY')&&!error.message.includes('synthetic-token'));
    },
};
cases.connect_while_uploading_keeps_structured_label=async()=>{
    const s=stateView();echo();vm.runInThisContext('uploadController.result=state.upload');
    loginFields();global.fetch=async()=>response({success:true,connection_token:'synthetic-token',student_name:'SYNTHETIC',data:structured});
    await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');assert.equal(s.source,'tarumt_intranet_live');assert.equal(s.upload,null);
};
(async()=>{await cases[which]();console.log(JSON.stringify({case:which,status:'passed'}));})().catch(error=>{console.error(error.stack);process.exitCode=1;});
