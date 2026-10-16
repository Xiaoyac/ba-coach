const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const cache={};function load(name){if(cache[name])return cache[name];const file=path.resolve(__dirname,'../lib',name+'.ts');const c={exports:{},crypto:require('node:crypto'),sessionStorage:{getItem:()=>null},require(n){if(n.startsWith('@/lib/'))return load(n.slice(6));return require(n)}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,c);return cache[name]=c.exports;}
const r=load('chatRecovery'),b=load('replyBinding');
const item={id:'g',sessionId:'s',userMessageId:1,text:'test'};
const detail={session_id:'s',messages:[{id:1,role:'user',content:'test'},{id:2,reply_to_message_id:1,role:'assistant',content:'opening',reply_status:'interrupted'}]};
assert.equal(b.hasDurableReplyForTurn(detail,item),false);
assert.equal(r.reconcileFailedSends([item],detail).length,1);
assert.equal(r.reconcileFailedSends([],detail)[0].userMessageId,1,'recovery survives page reload');
detail.messages[1].reply_status=null;
assert.equal(b.hasDurableReplyForTurn(detail,item),true);
assert.equal(r.reconcileFailedSends([item],detail).length,0);
assert.equal(b.hasDurableReplyForTurn({...detail,session_id:'another'},item),false);
assert.equal(b.hasDurableReplyForTurn(detail,{...item,userMessageId:null}),false);
for(const error of ['ServerOverloaded','TooManyRequests','rate_limit'])assert.match(r.safeGenerationError(error),/过载/);
for(const error of ['ServerOverloaded','timed out','unknown'])assert.doesNotMatch(r.safeGenerationError(error),/正在同步/);
for(const code of [409,500,502,503,504])assert.doesNotMatch(r.chatRequestError(code),/正在同步/);
assert.doesNotMatch(r.safeChatTransportError(new Error('connection reset')),/正在同步/);
console.log('PASS: complete/partial identity, reload recovery, wrong conversation, missing receipt, error classification and terminal wording');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const chatFile=path.resolve(__dirname,'../components/Chat.tsx');
const stub=()=>null;
const component={exports:{},require(n){
 if(n==='@/lib/useParticipantStyle')return {useParticipantStyle:()=>false};
 if(n==='@/lib/chatRecovery')return r;
 if(n.startsWith('@/'))return new Proxy({__esModule:true,default:stub},{get:(t,k)=>k in t?t[k]:stub});
 return require(n);
}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(chatFile,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,component);
const props={messages:[],routing:{},sessionId:'s',failedSends:[item],onRetryFailedSend:()=>{},onSend:()=>{},accountRole:'user'};
const html=renderToStaticMarkup(React.createElement(component.exports.default,props));
assert.match(html,/重新发送并生成回复/);assert.match(html,/<details[^>]*open/);assert.doesNotMatch(html,/正在同步|尚未同步到回复/);
const unknown=renderToStaticMarkup(React.createElement(component.exports.default,{...props,failedSends:[{...item,userMessageId:null}]}));
assert.doesNotMatch(unknown,/重新发送并生成回复/);assert.match(unknown,/放回输入框/);
console.log('PASS: actual recovery UI is expanded, exposes explicit retry, unknown receipts cannot auto-resend');
for(const role of ['user','admin']){
 const retryHtml=renderToStaticMarkup(React.createElement(component.exports.default,{...props,accountRole:role,generationNotice:'重新连接中(网络超时)'}));
 assert.match(retryHtml,/role="status"[^>]*>重新连接中\(网络超时\)/);
}
console.log('PASS: member and admin retry notice shows exact requested text');
