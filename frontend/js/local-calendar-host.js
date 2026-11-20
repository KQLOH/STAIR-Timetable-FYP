/* Dedicated loopback sandbox bootstrap. No browser-stored grant or bearer. */
(function(global){
    'use strict';
    global.addEventListener('DOMContentLoaded',()=>{
        const base='http://127.0.0.1:8022',prefix='/api/calendar/v1/test-only';
        if(global.location.origin!==base)return;
        const controller=global.calendarSandboxController=new global.STAIRSandbox.Controller({base});
        controller.setup();
        const request=document.getElementById('requestSandboxAccess'),connect=document.getElementById('connectSandboxHost');
        const instructions=document.getElementById('sandboxAccessInstructions');let binding='';
        connect.disabled=true;
        request.addEventListener('click',async()=>{
            request.disabled=true;binding='';controller.forget();connect.disabled=true;
            try{
                const response=await fetch(base+'/__local_calendar__/pending',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({acknowledge_test_only:true}),credentials:'same-origin',redirect:'error',cache:'no-store'});
                const pending=await response.json();
                if(!response.ok || pending.authorized!==false || !/^[A-Za-z0-9_-]{43}$/.test(pending.binding) || !/^[A-Za-z0-9_-]{24}$/.test(pending.nonce))throw Error('Access request was refused. Use the supported local launcher and try again.');
                binding=pending.binding;connect.disabled=false;
                instructions.textContent='In your own STAIR sandbox launcher console, personally type: authorize-sandbox '+pending.nonce+' within 120 seconds. Then click Connect approved access. This grants sandbox access only; it does not approve any calendar action.';
                document.getElementById('sandboxAccessDetails').open=true;
                controller.status('Access requested. Follow Owner access instructions, then connect. No action has been approved.');
            }catch(error){controller.status(error.message);}finally{request.disabled=false;}
        });
        connect.addEventListener('click',event=>{
            event.stopImmediatePropagation();
            if(!binding){controller.status('Request sandbox access and obtain console consent first.');return;}
            controller.attachTrustedHostTransport((url,options)=>{
                const target=new URL(url);
                if(target.origin!==base || !target.pathname.startsWith(prefix+'/') || target.search || target.hash || target.username || target.password)throw Error('Unapproved sandbox destination.');
                const headers=new Headers(options.headers);if(headers.has('Authorization'))throw Error('Direct credentials are not supported.');
                headers.set('X-STAIR-Calendar-Binding',binding);
                return fetch(target.href,{...options,headers,credentials:'same-origin',redirect:'error',cache:'no-store'});
            });
            controller.connect().then(()=>{if(controller.session){instructions.textContent='Owner access is connected. Each exact preview still needs separate approval. After reload or restart, request fresh console authorization.';document.getElementById('sandboxAccessDetails').open=false;}});
        },{capture:true});
        global.addEventListener('pagehide',()=>{binding='';});
    });
})(window);
