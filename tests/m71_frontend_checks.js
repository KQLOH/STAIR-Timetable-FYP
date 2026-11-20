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
    async structured_reconstruction_parse_error_never_echoes_private_content(){
        const data=JSON.parse(JSON.stringify(structured));data.application.reconstruction_json='PRIVATE_RESPONSE_CANARY';
        assert.throws(()=>STAIRIntranet.structuredPacket(data),e=>e.message==='Invalid structured assessment binding.');
    },
    async structured_stale_policy_is_rejected_before_source_adoption(){
        const data=JSON.parse(JSON.stringify(structured));data.application.policy[0].revision_id='00000000-0000-4000-8000-000000000000';
        assert.throws(()=>STAIRIntranet.structuredPacket(data));
    },
    async portal_redirect_reason_is_readable_but_never_echoes_private_data(){
        loginFields();global.fetch=async()=>({...response({stage:'LOGIN_SUBMISSION',reason:'ORIGIN_NOT_ALLOWED',detail:'PRIVATE_COOKIE_CANARY'}),ok:false,status:502});
        await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');const text=get('intranetLoginError').textContent;
        assert(text.includes('outside the approved institutional origin'));assert(!text.includes('PRIVATE_COOKIE_CANARY'));
    },
    async portal_unknown_reason_cannot_leak_private_or_prototype_values(){
        for(const reason of ['PRIVATE_COOKIE_CANARY','constructor','__proto__']){
            loginFields();global.fetch=async()=>({...response({stage:'LOGIN_SUBMISSION',reason}),ok:false,status:502});
            await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');const text=get('intranetLoginError').textContent;
            assert(!text.includes('PRIVATE_COOKIE_CANARY'));assert(!text.includes('function'));assert(!text.includes('[object'));
        }
    },
    async portal_502_displays_only_fixed_stage_explanation(){
        loginFields();global.fetch=async()=>({...response({detail:'PRIVATE_CANARY',stage:'SESSION_NAVIGATION'}),ok:false,status:502});
        await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');
        const text=get('intranetLoginError').textContent;assert(text.includes('502'));assert(text.includes('timetable navigation'));assert(!text.includes('PRIVATE_CANARY'));
    },
    async portal_502_unknown_stage_cannot_echo_private_data(){
        loginFields();global.fetch=async()=>({...response({detail:'PRIVATE_CANARY',stage:'PRIVATE_STAGE_CANARY'}),ok:false,status:502});
        await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');
        const text=get('intranetLoginError').textContent;assert(text.includes('502'));assert(!text.includes('PRIVATE'));
    },
    async invalid_json_does_not_echo_remote_content(){
        loginFields();global.fetch=async()=>({...response({}),json:async()=>{throw Error('PRIVATE_RESPONSE_CANARY');}});
        await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');
        assert(!get('intranetLoginError').textContent.includes('PRIVATE_RESPONSE_CANARY'));assert(get('intranetLoginError').textContent.includes('Invalid TAR UMT response'));
    },
    async session_invalid_json_does_not_echo_remote_content(){
        global.fetch=async()=>({...response({}),json:async()=>{throw Error('PRIVATE_RESPONSE_CANARY');}});
        await assert.rejects(STAIRIntranet.sessionRequest('http://synthetic','/api/intranet/timetable','SYNTHETIC'),error=>!error.message.includes('PRIVATE_RESPONSE_CANARY'));
    },
    async rejected_connect_packet_closes_new_session_preserves_old_source(){
        vm.runInThisContext('loadSampleData()');const s=stateView();s.connectionToken='synthetic-old';const old=s.events;
        loginFields();const closed=[];global.fetch=async()=>response({success:true,connection_token:'synthetic-invalid',data:{events:null}});
        global.STAIRIntranet={...STAIRIntranet,sessionRequest:async(base,path,token)=>{closed.push(token);return {success:true};}};
        await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');
        assert.equal(s.events,old);assert.equal(s.source,'sample');assert.equal(s.connectionToken,'synthetic-old');assert.deepEqual(closed,['synthetic-invalid']);
    },
    async password_cleared_while_login_pending_and_redirects_refused(){
        loginFields();const d=deferred();let sent;
        global.fetch=(url,options)=>{sent=options;return d.promise;};
        const pending=vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');
        assert.equal(get('intranetPassword').value,'');assert.equal(sent.credentials,'omit');assert.equal(sent.redirect,'error');assert.equal(sent.cache,'no-store');
        assert.equal(JSON.parse(sent.body).password,'SYNTHETIC-PASSWORD');d.resolve({...response({}),ok:false,status:401});await pending;
    },
    async login_errors_do_not_echo_private_response(){
        loginFields();global.fetch=async()=>({...response({detail:'PRIVATE_CANARY'}),ok:false,status:500});
        await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');
        assert(!get('intranetLoginError').textContent.includes('PRIVATE_CANARY'));assert(get('intranetLoginError').textContent.includes('500'));
    },
    async previous_session_cleanup_cannot_interrupt_new_timetable_commit(){
        loginFields();const s=stateView();s.connectionToken='synthetic-old';const d=deferred();
        global.fetch=async()=>response({success:true,connection_token:'synthetic-new',data:structured});
        global.STAIRIntranet={...STAIRIntranet,sessionRequest:()=>d.promise};
        const pending=vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');await new Promise(r=>setImmediate(r));
        assert.equal(s.connectionToken,'synthetic-new');assert.equal(s.events.length,structured.events.length);assert.equal(s.source,'tarumt_intranet_live');
        vm.runInThisContext('loadSampleData()');d.resolve({success:true});await pending;assert.equal(s.source,'sample');
    },
    async missing_token_cannot_replace_current_source(){
        vm.runInThisContext('loadSampleData()');loginFields();global.fetch=async()=>response({success:true,data:structured});
        await vm.runInThisContext('connectLiveIntranet({preventDefault(){}})');assert.equal(stateView().source,'sample');assert.equal(stateView().connectionToken,null);
    },
    async session_transport_does_not_forward_cookies_or_follow_redirects(){
        let options;global.fetch=async(url,sent)=>{options=sent;return response({success:true});};
        await STAIRIntranet.sessionRequest('http://synthetic','/api/intranet/disconnect','SYNTHETIC');
        assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');
    },

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
