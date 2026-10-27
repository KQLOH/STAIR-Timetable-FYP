/* Isolated fake-DOM security assertions; no live browser or operator pairing. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function environment(){
    const elements=[],handlers={},calls=[],attached={};
    class Element{
        constructor(){this.children=[];this.style={};this.textContent='';this.id='';this.listeners={};elements.push(this);}
        setAttribute(){} append(...items){this.children.push(...items);} prepend(e){this.children.unshift(e);}
        addEventListener(name,fn){this.listeners[name]=fn;}
    }
    const main=new Element();
    const context={URL,Headers,document:{createElement:()=>new Element(),querySelector:()=>main,addEventListener:(name,fn)=>{handlers[name]=fn;}},
        reviewController:{attachTrustedHostTransport:fn=>{attached.review=fn;}},calendarSandboxController:{attachTrustedHostTransport:fn=>{attached.calendar=fn;}},
        localStorage:new Proxy({},{get(){throw Error('Storage forbidden');}}),sessionStorage:new Proxy({},{get(){throw Error('Storage forbidden');}}),
        fetch:async(url,options)=>{
            calls.push({url,options});
            if(url.includes('/pending/'))return {ok:true,json:async()=>({authorized:false,pairing_nonce:'N'.repeat(24),transport_binding:(url.endsWith('review')?'R':'C').repeat(43)})};
            return {status:200,ok:true,json:async()=>({})};
        }};
    vm.runInNewContext(fs.readFileSync('tests/s4b_host_bootstrap.js','utf8'),context);
    handlers.DOMContentLoaded();
    return {context,attached,calls,elements,byId:id=>elements.find(e=>e.id===id)};
}
(async()=>{
    const env=environment();assert(Object.keys(env.attached).length===0&&env.calls.length===0);
    await env.byId('s4b-pair-review').listeners.click();
    assert(env.calls.length===1&&typeof env.attached.review==='function'&&!env.attached.calendar);
    assert(env.byId('s4b-pair-status-review').textContent.includes('pair review '));
    assert(!env.elements.some(e=>e.textContent.includes('R'.repeat(43))));
    const request=env.attached.review;
    await request('http://127.0.0.1:8017/api/review/session',{method:'GET'});
    const options=env.calls.at(-1).options;
    assert(options.credentials==='same-origin'&&options.redirect==='error'&&options.cache==='no-store');
    assert(options.headers.get('X-STAIR-Demo-Transport')==='review');
    assert(options.headers.get('X-STAIR-Demo-Binding')==='R'.repeat(43));
    assert(!options.headers.has('Authorization'));
    for(const url of ['http://evil.invalid/api/review/session','http://127.0.0.1:8000/api/review/session',
        'http://u:p@127.0.0.1:8017/api/review/session','http://127.0.0.1:8017/api/calendar/v1/test-only/session',
        'http://127.0.0.1:8017/api/review/session#x','http://127.0.0.1:8017/api/review/session?token=x',
        'http://127.0.0.1:8017/api/review/grants']){
        const count=env.calls.length;await assert.rejects(request(url,{method:'GET'}));assert(env.calls.length===count);
    }
    await assert.rejects(request('http://127.0.0.1:8017/api/review/session',{method:'GET',headers:{Authorization:'Bearer forbidden'}}));
    await env.byId('s4b-pair-calendar').listeners.click();assert(typeof env.attached.calendar==='function');
    env.context.fetch=async()=>({status:403});
    await request('http://127.0.0.1:8017/api/review/session',{method:'GET'});
    await assert.rejects(request('http://127.0.0.1:8017/api/review/session',{method:'GET'}));
    const fresh=environment();assert(fresh.calls.length===0&&Object.keys(fresh.attached).length===0);
    console.log('S4B frontend transport security assertions passed.');
})().catch(()=>{console.error('S4B frontend security assertion failed (credentials redacted).');process.exitCode=1;});
