/* S4B-F1 synthetic client replay. No HTTP authority, grant, or calendar mutation. */
(function(global){
    'use strict';
    const clone=value=>JSON.parse(JSON.stringify(value));
    const check=(condition,message)=>{if(!condition)throw Error(message);};
    function createHarness(fixture){
        const get=id=>global.document.getElementById(id),calls=[];
        let pending=null;
        const response=(data,status=200)=>({ok:status===200,status,json:async()=>clone(data)});
        async function host(url,options){
            const path=new URL(url).pathname.split('/test-only')[1];
            const body=options.body?JSON.parse(options.body):null;calls.push({path,body});
            let data;
            if(path==='/session')data=fixture.session;
            else if(path==='/preview')data=body.fixture_id==='update'?fixture.update:fixture.create;
            else if(path==='/approve'){
                const p=controller.plan;
                for(const key of ['preview_digest','snapshot_digest','input_digest','target_sandbox_id'])check(body[key]===p[key],key+' binding changed');
                check(JSON.stringify(body.operation_keys)===JSON.stringify(p.operations.map(o=>o.operation_key)),'operation binding changed');
                check(body.owner_confirmation===true,'explicit owner confirmation missing');
                data=p.preview_digest===fixture.update.preview_digest?fixture.update_approval:fixture.approval;
            }else if(path==='/history')data=fixture.history_approved;
            else if(path==='/state')data=fixture.state_before;
            else if(path==='/execute')data=fixture.executed;
            else throw Error('Unexpected synthetic request '+path);
            if(pending?.path===path){
                const gate=pending;pending=null;gate.enter();
                if(gate.body)return {ok:true,status:200,json:async()=>clone((await gate.promise)?.data ?? data)};
                const replacement=await gate.promise;
                return response(replacement?.data ?? data,replacement?.status ?? 200);
            }
            return response(data);
        }
        const controller=new global.STAIRSandbox.Controller({base:'http://synthetic.invalid',hostRequest:host});controller.setup();
        function hold(path,{body=false}={}){
            check(!pending,'Only one synthetic delay is allowed');let enter,resolve,reject;
            const entered=new Promise(r=>enter=r),promise=new Promise((a,b)=>{resolve=a;reject=b;});
            pending={path,body,enter,promise};return {entered,release:resolve,fail:reject};
        }
        function select(value){get('sandboxFixture').value=value;get('sandboxFixture').dispatchEvent(new Event('change'));}
        const status=()=>get('sandboxWorkflowStatus').textContent;
        function invalidated(){
            check(/new preview and explicit owner approval are required/i.test(status()),'fresh preview/approval guidance missing: '+status());
            check(!/Exact TEST_ONLY approval recorded|Waiting for/.test(status()),'stale approval/loading message restored');
            check(controller.plan===null && controller.approval===null,'stale executable state restored');
            check(!get('sandboxOwnerConfirmation').checked && get('executeSandbox').disabled && get('approveSandbox').disabled,'approval gate restored');
            check(controller.session!==null,'stale response ended the current session');
            check(get('sandboxSource').textContent.includes('SYNTHETIC ROOM C'),'new source was replaced');
        }
        async function begin(){await controller.connect();await controller.preview();}
        async function approve(){await begin();get('sandboxOwnerConfirmation').checked=true;await controller.approve();check(controller.approval!==null,'setup approval missing');}
        const count=path=>calls.filter(c=>c.path===path).length;
        return {get,calls,controller,hold,select,status,invalidated,begin,approve,count};
    }
    const cases={
        async switch_after_approval(h){
            await h.approve();check(/Exact TEST_ONLY approval recorded/.test(h.status()),'original approval missing');
            const before=h.calls.length;h.select('update');h.invalidated();
            check(!h.controller.uncertain && !h.controller.busy,'selection left stale busy/uncertain state');
            check(h.calls.length===before,'selection unexpectedly made a backend request');
        },
        async switching_back_requires_fresh_preview_and_confirmation(h){
            await h.approve();h.select('update');h.select('base');
            const before=h.calls.length;await h.controller.approve();await h.controller.execute();
            check(h.calls.length===before,'old approval was reused after switching back');
            await h.controller.preview();check(!h.get('sandboxOwnerConfirmation').checked,'confirmation was reused');
            await h.controller.approve();check(h.count('/approve')===1,'preview implicitly approved');
            h.get('sandboxOwnerConfirmation').checked=true;await h.controller.approve();check(h.count('/approve')===2,'fresh explicit approval failed');
        },
        async late_preview_success(h){
            await h.controller.connect();const gate=h.hold('/preview'),running=h.controller.preview();await gate.entered;
            h.select('update');h.invalidated();gate.release();await running;h.invalidated();
            check(h.get('sandboxPreview').textContent==='','late preview was rendered');
        },
        async late_approval_success(h){
            await h.begin();h.get('sandboxOwnerConfirmation').checked=true;
            const gate=h.hold('/approve'),running=h.controller.approve();await gate.entered;
            const history=h.count('/history');h.select('update');gate.release();await running;h.invalidated();
            check(h.count('/history')===history,'stale approval started a new history request');
        },
        async late_approval_403(h){await lateApprovalFailure(h,403);},
        async late_approval_409(h){await lateApprovalFailure(h,409);},
        async late_approval_transport_failure(h){
            await h.begin();h.get('sandboxOwnerConfirmation').checked=true;
            const gate=h.hold('/approve'),running=h.controller.approve();await gate.entered;
            h.select('update');gate.fail(Error('Synthetic connection failure'));await running;h.invalidated();
        },
        async late_approval_body_decode(h){
            await h.begin();h.get('sandboxOwnerConfirmation').checked=true;
            const gate=h.hold('/approve',{body:true}),running=h.controller.approve();await gate.entered;
            h.select('update');gate.release();await running;h.invalidated();
        },
        async late_approval_history_success(h){await lateHistory(h,'/history');},
        async late_approval_state_success(h){await lateHistory(h,'/state');},
        async late_approval_history_failure(h){await lateHistory(h,'/history',true);},
        async late_connect_history(h){
            const gate=h.hold('/history'),running=h.controller.connect();await gate.entered;
            h.select('update');gate.release();await running;h.invalidated();
        },
        async late_execute_success(h){
            await h.approve();const gate=h.hold('/execute'),running=h.controller.execute();await gate.entered;
            h.select('update');gate.release();await running;h.invalidated();
            check(h.count('/execute')===1,'execution was automatically retried');
            check(h.get('sandboxExecution').textContent==='' && !h.controller.uncertain,'old outcome restored the new view');
        },
        async late_execute_failure(h){
            await h.approve();const gate=h.hold('/execute'),running=h.controller.execute();await gate.entered;
            h.select('update');gate.fail(Error('Synthetic timeout'));await running;h.invalidated();
            check(!h.controller.uncertain,'old execution failure changed the new fixture state');
            check(h.count('/execute')===1,'execution was automatically retried');
        },
        async current_403_still_ends_session(h){
            await h.begin();h.get('sandboxOwnerConfirmation').checked=true;
            const gate=h.hold('/approve'),running=h.controller.approve();await gate.entered;
            gate.release({status:403,data:{detail:'TEST_ONLY_CALENDAR_UNAUTHORIZED'}});await running;
            check(h.controller.session===null && h.get('executeSandbox').disabled,'current authorization refusal was ignored');
            check(/refused or expired/i.test(h.status()),'current refusal guidance missing');
        },
        async current_409_still_reports_stale_preview(h){
            await h.begin();h.get('sandboxOwnerConfirmation').checked=true;
            const gate=h.hold('/approve'),running=h.controller.approve();await gate.entered;
            gate.release({status:409,data:{detail:'STALE_PREVIEW'}});await running;
            check(h.controller.session!==null && h.controller.approval===null && h.get('executeSandbox').disabled,'current stale-preview gate changed');
            check(/fresh preview/i.test(h.status()),'current stale-preview error was hidden');
        }
    };
    async function lateApprovalFailure(h,status){
        await h.begin();h.get('sandboxOwnerConfirmation').checked=true;
        const gate=h.hold('/approve'),running=h.controller.approve();await gate.entered;
        h.select('update');gate.release({status,data:{detail:status===403?'TEST_ONLY_CALENDAR_UNAUTHORIZED':'STALE_PREVIEW'}});await running;h.invalidated();
    }
    async function lateHistory(h,path,fail=false){
        await h.begin();h.get('sandboxOwnerConfirmation').checked=true;
        const gate=h.hold(path),running=h.controller.approve();await gate.entered;
        check(/Exact TEST_ONLY approval recorded/.test(h.status()),'approval response was not reached');
        const states=h.count('/state');h.select('update');h.invalidated();
        if(fail)gate.fail(Error('Synthetic history failure'));else gate.release();
        await running;h.invalidated();if(path==='/history')check(h.count('/state')===states,'stale history started a state request');
    }
    global.S4BF1Checks={createHarness,cases};
    if(typeof module!=='undefined' && module.exports){
        const fs=require('node:fs'),vm=require('node:vm');
        class Element{
            constructor(tag='div'){Object.assign(this,{tagName:tag,children:[],listeners:{},disabled:false,checked:false,_text:'',_value:''});}
            set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
            set value(v){this._value=String(v);}get value(){return this._value || (this.tagName==='select'?this.children[0]?.value || '':'');}
            append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[];this._text='';this._value='';this.append(...items);}
            setAttribute(){}addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);}dispatchEvent(event){for(const fn of this.listeners[event.type] || [])fn(event);}
        }
        const nodes=new Map();for(const m of fs.readFileSync('frontend/index.html','utf8').matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g))nodes.set(m[2],new Element(m[1]));
        global.document={getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),querySelector:()=>null};global.window=global;global.addEventListener=()=>{};
        vm.runInThisContext(fs.readFileSync(process.argv[4] || 'frontend/js/sandbox.js','utf8'));
        const h=createHarness(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))),name=process.argv[3];
        (async()=>{try{await cases[name](h);console.log(JSON.stringify({case:name,status:'PASS'}));}finally{h.controller.forget();}})().catch(error=>{console.error(error.stack);process.exitCode=1;});
    }
})(typeof window!=='undefined'?window:globalThis);
