/* Isolated S4B assets only. Binding credentials stay in workflow closures. */
(function(){
    'use strict';
    const origin='http://127.0.0.1:8017';
    const prefixes={review:'/api/review',calendar:'/api/calendar/v1/test-only'};
    const patterns={
        review:{GET:/^\/api\/review\/(session|queue|tasks\/[0-9a-f-]{36}(\/(snapshot|history))?)$/i,POST:/^\/api\/review\/(ingest|session\/end|tasks\/[0-9a-f-]{36}\/(transition|corrections|revalidate))$/i},
        calendar:{GET:/^\/api\/calendar\/v1\/test-only\/(session|history|state)$/,POST:/^\/api\/calendar\/v1\/test-only\/(session\/end|preview|approve|execute|reconcile|revoke-approval)$/}
    };
    function transport(workflow,credential){
        let binding=credential;
        return async function(url,options={}){
            const target=new URL(url,origin),method=(options.method||'GET').toUpperCase();
            if(target.origin!==origin||target.username||target.password||target.hash||!patterns[workflow][method]?.test(target.pathname)||target.href.includes('%'))throw Error('S4B transport route refused');
            if(target.search&&(workflow!=='review'||method!=='GET'||target.pathname!==prefixes.review+'/queue'||[...target.searchParams].some(([k,v])=>!['offset','limit'].includes(k)||!/^\d+$/.test(v))))throw Error('S4B transport query refused');
            const headers=new Headers(options.headers||{});
            for(const name of ['Authorization','Cookie','X-STAIR-Demo-Binding','X-STAIR-Demo-Transport'])if(headers.has(name))throw Error('S4B credential substitution refused');
            if(!binding)throw Error('S4B channel ended');
            headers.set('X-STAIR-Demo-Binding',binding);headers.set('X-STAIR-Demo-Transport',workflow);
            const response=await fetch(target.href,{...options,method,headers,credentials:'same-origin',redirect:'error',cache:'no-store'});
            if(response.status===403||target.pathname===prefixes[workflow]+'/session/end')binding='';
            return response;
        };
    }
    document.addEventListener('DOMContentLoaded',()=>{
        const panel=document.createElement('section');panel.setAttribute('aria-label','S4B manual host pairing');
        panel.style.cssText='padding:14px;margin:12px;border:1px solid #ccd7d5;border-radius:10px;background:#fff;overflow-wrap:anywhere';
        const title=document.createElement('strong');title.textContent='S4B TEST_ONLY — explicit manual console pairing';panel.append(title);
        for(const workflow of ['review','calendar']){
            const row=document.createElement('div');row.style.cssText='display:flex;gap:12px;flex-wrap:wrap;margin-top:10px;align-items:center';
            const button=document.createElement('button');button.type='button';button.id='s4b-pair-'+workflow;button.className='button secondary';button.textContent='Request '+workflow+' pairing';
            const status=document.createElement('p');status.id='s4b-pair-status-'+workflow;status.setAttribute('role','status');status.textContent='Unpaired — no authority.';
            button.addEventListener('click',async()=>{
                button.disabled=true;status.textContent='Requesting an unauthorized pending channel…';
                try{
                    const response=await fetch(origin+'/__s4b_host__/pending/'+workflow,{method:'POST',credentials:'same-origin',redirect:'error',cache:'no-store',headers:{'Content-Type':'application/json'},body:'{}'});
                    if(!response.ok)throw Error('Pairing request refused');
                    const data=await response.json();
                    if(data.authorized!==false||!/^[-A-Za-z0-9_]{24}$/.test(data.pairing_nonce)||!/^[-A-Za-z0-9_]{43}$/.test(data.transport_binding))throw Error('Invalid pending channel');
                    const request=transport(workflow,data.transport_binding);data.transport_binding='';
                    if(workflow==='review')reviewController.attachTrustedHostTransport(request);
                    else calendarSandboxController.attachTrustedHostTransport(request);
                    status.textContent='You must type in the S4B console: pair '+workflow+' '+data.pairing_nonce+' (within 120 seconds). Then open the protected queue or connect the sandbox host. No pairing is automatic.';
                }catch{status.textContent='Pairing request refused or unavailable. No authority was assumed.';}
                finally{button.disabled=false;}
            });
            row.append(button,status);panel.append(row);
        }
        document.querySelector('.main-area').prepend(panel);
    });
})();
