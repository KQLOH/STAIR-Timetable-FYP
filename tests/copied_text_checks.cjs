const assert=require('node:assert/strict');
require('../frontend/js/upload.js');const U=STAIRUpload;
(async()=>{
 const text='CS101 Monday 2033-02-07 09:00-10:00 Lecture Room: A101\r\n\tLecturer: Renée';
 const file=U.copiedTextFile(text);assert.equal(file.type,'text/plain');assert.equal(file.name,'copied-timetable.txt');assert.equal(await file.text(),text);
 const valid=await U.validateFile(file);assert.equal(valid.text,text);assert.equal(valid.mime,'text/plain');
 assert.throws(()=>U.copiedTextFile('   '),/Paste/);assert.throws(()=>U.copiedTextFile('x'.repeat(131073)),/128 KiB/);assert.throws(()=>U.copiedTextFile('é'.repeat(70000)),/128 KiB/);assert.throws(()=>U.copiedTextFile('x\n'.repeat(2049)),/2048/);assert.throws(()=>U.copiedTextFile('text\x00hidden'),/control/);
 await assert.rejects(U.validateFile(new File([new Uint8Array([0xff])],'bad.txt',{type:'text/plain'})),/UTF-8/);
 const previous={busy:false,stages:{},extraction:{source:{}}};const c=new U.Controller({fetcher:()=>{throw Error('Invalid text must not reach backend');}});c.result=previous;c.objectUrl='blob:kept';await assert.rejects(c.run(new File(['x\x00'],'bad.txt',{type:'text/plain'}),'evidence'),/control/);assert.equal(c.result,previous);assert.equal(c.objectUrl,'blob:kept');
 const nodes=[];const container={dataset:{},replaceChildren(){nodes.length=0;},append(...n){nodes.push(...n)}};
 global.document={getElementById:id=>id==='documentPreview'?container:null,createElement:tag=>({tagName:tag,textContent:'',setAttribute(){}})};
 const malicious='<img src=x onerror=alert(1)>';U.renderSource({mime:'text/plain',sourceText:malicious,previewUrl:'blob:source'});
 assert.equal(nodes[0].tagName,'pre');assert.equal(nodes[0].textContent,malicious);assert.equal(nodes[1].download,'copied-timetable.txt');assert.equal(nodes[1].href,'blob:source');assert(!nodes.some(n=>n.tagName==='img'));
 console.log('PASS copied-text byte preservation, UTF-8/size/line/control validation, safe original display/download, failed replacement preservation');
})().catch(e=>{console.error(e);process.exit(1)});
