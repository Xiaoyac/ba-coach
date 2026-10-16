const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
let incoming='';const c={exports:{},process:{env:{}},TextDecoder,AbortSignal,fetch:async()=>new Response(incoming),require(n){
 if(n==='@/lib/http')return {apiHeaders:()=>({}),checkAuthentication:()=>{}};
 if(n==='@/lib/chatRecovery')return {chatRequestError:()=>'',safeGenerationError:x=>x};
 return require(n);
}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../lib/api.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,c);
const frame=(e,p)=>`event: ${e}\ndata: ${JSON.stringify(p)}\n\n`;
(async()=>{
 for(const succeed of [true,false]){
 const events=[];
 incoming=Array.from({length:5},(_,i)=>frame('retry',{retrying:true,attempt:i+1,max_retries:5,generation_id:'g'})).join('')
 +frame('retry',{retrying:true,attempt:6,max_retries:5,generation_id:'g'})
 +frame('retry',{retrying:true,attempt:1,max_retries:5})
 +frame('retry',{retrying:false,attempt:5,max_retries:5,generation_id:'g'})
 +(succeed?frame('delta',{text:'回复'}):frame('error',{detail:'ServerOverloaded'}))
 +frame('persisted',{saved:succeed});
 await c.exports.streamChat({message:'test',session_id:'s'},{onRetry:x=>events.push(x),onPersisted:x=>assert.equal(x,succeed)});
 assert.equal(events.length,6);assert.equal(events.at(-1).retrying,false);
 assert.equal(events.filter(x=>x.retrying).map(x=>x.attempt).join(','),'1,2,3,4,5');
 }
 console.log('PASS: real SSE parser forwards five retries, validates payloads and closes on success/failure');
})().catch(e=>{console.error(e);process.exitCode=1});
