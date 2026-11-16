const assert=require('node:assert/strict');
require('../frontend/js/upload.js');
const config=STAIRUpload.readerConfiguration;
const noisy={pipeline:'B2',reconstruct:false,layout:false,blockGrid:true,ruledGrid:true,automatic:true,contextCorrection:true};
for(const [reader,blockGrid,ruledGrid,layout] of [['colored',true,false,false],['grid',false,true,false],['general',false,false,true]]){
 const c=config(reader,noisy);assert.equal(c.pipeline,'evidence');assert.equal(c.reconstruct,true);assert.equal(c.blockGrid,blockGrid);assert.equal(c.ruledGrid,ruledGrid);assert.equal(c.layout,layout);assert.equal(c.automatic,false);assert.equal(c.contextCorrection,reader==='colored');
}
assert.equal(config().automatic,false);assert.equal(config('colored').contextCorrection,true);assert.equal(config('colored',{contextCorrection:false}).contextCorrection,false);
for(const pipeline of STAIRUpload.PIPELINES)for(let mask=0;mask<32;mask++){
 const c=config('advanced',{pipeline,blockGrid:!!(mask&1),ruledGrid:!!(mask&2),automatic:!!(mask&4),contextCorrection:!!(mask&8),reconstruct:!!(mask&16),layout:true});
 assert(!(c.blockGrid&&c.ruledGrid));assert(!(c.automatic&&(c.blockGrid||c.ruledGrid)));
 if(pipeline!=='evidence'){assert.equal(c.blockGrid,false);assert.equal(c.ruledGrid,false);assert.equal(c.automatic,false);assert.equal(c.contextCorrection,false);}
 if(c.blockGrid||c.ruledGrid||c.automatic)assert.equal(c.pipeline,'evidence');
}
assert.equal(config('advanced',{pipeline:'evidence',automatic:true}).automatic,true);
assert.equal(config('advanced',{pipeline:'B1',reconstruct:false}).reconstruct,false);
assert.throws(()=>config('unsupported-reader'),/supported/);
console.log('PASS manual reader mapping, RT4 defaults, legacy readers, and 128 advanced combinations');
