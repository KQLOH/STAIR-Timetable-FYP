const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();let ready,attached,fetches=[],storage=0,refreshes=0;
class Element{
 constructor(){this.listeners={};this.children=[];this.disabled=false;this.textContent='';}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 setAttribute(){}append(...v){this.children.push(...v);}prepend(...v){this.children.unshift(...v);}addEventListener(k,v){this.listeners[k]=v;}
}
global.location={origin:'http://127.0.0.1:8020'};
global.document={addEventListener:(k,v)=>ready=v,createElement:()=>new Element(),querySelector:()=>new Element()};
global.reviewSource=()=>({packet:{source:global.source}});global.source={source_document_id:'synthetic:host-js',content_sha256:'a'.repeat(64)};
global.state={upload:{file:{name:'synthetic.pdf'}}};
global.reviewController={forget(){attached=null;this.session=null;},attachTrustedHostTransport(t){attached=t;},async refreshQueue(){refreshes++;this.session={mode:'TEST_ONLY'};}};
global.localStorage={setItem(){storage++;throw Error('No storage');},getItem(){storage++;throw Error('No storage');}};
global.fetch=async(url,opts)=>{fetches.push({url,opts});return {ok:true,json:async()=>({authorized:false,binding:'b'.repeat(43),nonce:'n'.repeat(24)})};};
vm.runInThisContext(fs.readFileSync('frontend/js/local-review-host.js','utf8'));ready();
const request=elements.get('requestLocalReview'),connect=elements.get('connectLocalReview');
const cases={
 async pending(){await request.listeners.click();assert.equal(fetches.length,1);assert.equal(attached,null);assert.equal(refreshes,0);assert.equal(connect.disabled,false);assert.match(elements.get('localReviewStatus').textContent,/personally run/);assert.equal(storage,0);},
 async transport(){await request.listeners.click();await connect.listeners.click();assert.equal(refreshes,1);await attached('http://127.0.0.1:8020/api/review/session');const last=fetches.at(-1);assert.equal(last.opts.credentials,'same-origin');assert.equal(last.opts.redirect,'error');assert.equal(last.opts.headers.get('X-STAIR-Review-Binding'),'b'.repeat(43));for(const url of ['https://evil.invalid/api/review/session','http://127.0.0.1:8020/api/calendar/v1/preview','http://127.0.0.1:8020/api/review/%2e%2e/grants'])await assert.rejects(attached(url));await assert.rejects(attached('http://127.0.0.1:8020/api/review/session',{headers:{Authorization:'forged'}}));assert.equal(storage,0);},
 async source_change(){await request.listeners.click();source={...source,content_sha256:'c'.repeat(64)};await connect.listeners.click();assert.equal(refreshes,0);assert.equal(attached,null);assert.match(elements.get('localReviewStatus').textContent,/source changed/);},
 async no_file(){state.upload=null;await request.listeners.click();assert.equal(fetches.length,0);assert.equal(attached,undefined);assert.match(elements.get('localReviewStatus').textContent,/Upload the original/);}
};
cases[process.argv[2]]().then(()=>console.log('PASS '+process.argv[2])).catch(e=>{console.error(e);process.exit(1)});
