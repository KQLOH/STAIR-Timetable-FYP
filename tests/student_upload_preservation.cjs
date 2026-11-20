const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
vm.runInThisContext(fs.readFileSync('frontend/js/upload.js','utf8'));
(async()=>{
 for(const file of [new File(['x'],'unsupported.exe'),new File(['text\x00hidden'],'binary.txt',{type:'text/plain'}),new File([new Uint8Array([0xff])],'invalid-utf8.txt',{type:'text/plain'}),new File(['wrong'],'bad.png',{type:'image/png'}),new File([],'empty.pdf',{type:'application/pdf'})]){
  let requests=0,notices=0;
  const c=new STAIRUpload.Controller({fetcher:()=>{requests++;throw Error('No request expected')},notify:()=>{notices++;}});
  const retained={busy:false,stages:{calendar:'BLOCKED_READ_ONLY'},extraction:{source:{source_document_id:'synthetic:kept'}},reviewRevision:2};
  c.result=retained;c.objectUrl='blob:synthetic-retained';c.abortController=new AbortController();
  await assert.rejects(c.run(file,'evidence'));
  assert.equal(c.result,retained);assert.equal(c.objectUrl,'blob:synthetic-retained');
  assert.equal(c.sequence,0);assert.equal(c.abortController.signal.aborted,false);
  assert.equal(requests,0);assert.equal(notices,0);
 }
 console.log('Unsupported, corrupt-signature and empty replacement retain current source/review work');
})().catch(e=>{console.error(e);process.exit(1)});
