export type Station={site:string;name:string;nodes:string[];node_keys:string[];user_api:string;admin_api:string;reference_site:string;local_demo:boolean;extension_id:string}
export type Policy={minimum:number;maximum:number;k:number;j:number;fragments:number[];terms:string[];shapes:number[];positions:number[][];snapshot:string|null;label:string}
export type Context={protocol:string;site:string;op:string;account:string|null;nonce:string;policy_version:number;account_version:number;client_key:string;params:Record<string,unknown>}
export type Event={party:number;receipt:Record<string,any>;signature:string}
export const hex=(value:ArrayBuffer|Uint8Array)=>Array.from(value instanceof Uint8Array?value:new Uint8Array(value),v=>v.toString(16).padStart(2,'0')).join('')
export const bytes=(value:string)=>Uint8Array.from(value.match(/../g)||[],v=>parseInt(v,16))
export const randomHex=(size=32)=>hex(crypto.getRandomValues(new Uint8Array(size)))
export function canonical(value:unknown):string{
 const ordered=(v:any):any=>Array.isArray(v)?v.map(ordered):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,ordered(v[k])])):v
 return JSON.stringify(ordered(value)).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'))
}
export async function request<T=any>(url:string,body?:unknown,session?:string):Promise<T>{
 const response=await fetch(url,{method:body===undefined?'GET':'POST',headers:{...(body===undefined?{}:{'Content-Type':'application/json'}),...(session?{Authorization:'Bearer '+session}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})})
 const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));return data
}
function shares(password:string,prime:bigint):string[][]{
 if(password.length>64||password.length<8||!/^[\x20-\x7e]+$/.test(password))throw Error('请使用 8–64 个可打印 ASCII 字符；不会截断口令。')
 const result:string[][]=[[],[],[]]
 const sample=()=>{let n;do{n=BigInt('0x'+randomHex(10))}while(n>=((1n<<80n)/prime)*prime);return n%prime}
 for(let i=0;i<64;i++){const value=i<password.length?password.charCodeAt(i):0;for(let b=0;b<8;b++){const slope=sample(),bit=BigInt((value>>b)&1);for(let p=0;p<3;p++)result[p].push(String((bit+BigInt(p+1)*slope)%prime))}}
 return result
}
async function keypair(){return await crypto.subtle.generateKey('Ed25519',true,['sign','verify']) as CryptoKeyPair}
export async function recoveryCredential(){const pair=await keypair();return {publicKey:hex(await crypto.subtle.exportKey('raw',pair.publicKey)),privateKey:hex(await crypto.subtle.exportKey('pkcs8',pair.privateKey))}}
export type OperationResult={outcome:string;result:any;session?:string;context:Context;events:Event[]}
export type PendingOperation={station:Station;api:string;context:Context;query:string}
export async function operation(station:Station,api:string,op:string,account:string|null,policyVersion:number,accountVersion:number,params:Record<string,unknown>,passwords:Record<string,string>,session:string|undefined,progress:(text:string)=>void,nonce?:string,recoveryPrivate?:string,onPending?:(pending:PendingOperation)=>void):Promise<OperationResult>{
 const pair=await keypair(),client_key=hex(await crypto.subtle.exportKey('raw',pair.publicKey))
 const context:Context={protocol:'zipfguard-tool-v1',site:station.site,op,account,nonce:nonce||randomHex(),policy_version:policyVersion,account_version:accountVersion,client_key,params:{...params}}
 if(recoveryPrivate){const key=await crypto.subtle.importKey('pkcs8',bytes(recoveryPrivate),'Ed25519',false,['sign']);context.params.recovery_signature=hex(await crypto.subtle.sign('Ed25519',key,new TextEncoder().encode(canonical(context))))}
 progress('检查三个计算服务的身份与状态…')
 const health=await Promise.all(station.nodes.map(url=>request(url+'/health')))
 if(health.some((h,i)=>!h.ready||h.party!==i||h.maximum!==64)||new Set(health.map(h=>h.field_prime)).size!==1)throw Error('三方尚未就绪，未提交本次口令。')
 const prime=BigInt(health[0].field_prime),fields:Record<string,string[][]>={}
 for(const [name,password] of Object.entries(passwords))fields[name]=shares(password,prime)
 progress('取得限次凭据，分别向三个节点发送一份秘密…')
 const ticket=await request(api+'/ticket',{context},session)
 await Promise.all(station.nodes.map(async(url,i)=>{const envelope={context,fields:Object.fromEntries(Object.entries(fields).map(([name,parts])=>[name,parts[i]])),salt:randomHex(),ticket:ticket.ticket};const signature=hex(await crypto.subtle.sign('Ed25519',pair.privateKey,new TextEncoder().encode(canonical(envelope))));return request(url+'/submit',{...envelope,signature})}))
 for(const parts of Object.values(fields))for(const part of parts)part.fill('0')
 const pending={station,api,context,query:ticket.query};onPending?.(pending)
 return finishOperation(pending,session,progress)
}
export async function finishOperation(pending:PendingOperation,session:string|undefined,progress:(text:string)=>void):Promise<OperationResult>{
 const {station,api,context,query}=pending
 progress('三方正在联合计算；请保持此页面打开…')
 const events=await Promise.all(station.nodes.map(async(url)=>{const deadline=Date.now()+600000;while(Date.now()<deadline){const state=await request(url+'/result/'+context.nonce,undefined,query);if(state.status==='completed')return state.event as Event;await new Promise(resolve=>setTimeout(resolve,700))}throw Error('结果暂时未知。请使用继续查询，避免重复操作。')}))
 progress('独立核验三方回执…')
 const digest=hex(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(context))))
 for(let i=0;i<3;i++){const event=events[i],receipt=event.receipt;const publicKey=await crypto.subtle.importKey('raw',bytes(station.node_keys[i]),'Ed25519',false,['verify']);if(event.party!==i||receipt.context_digest!==digest||receipt.site!==station.site||receipt.nonce!==context.nonce||canonical(receipt)!==canonical(events[0].receipt)||!await crypto.subtle.verify('Ed25519',publicKey,bytes(event.signature),new TextEncoder().encode(canonical(receipt))))throw Error('三方身份或回执不一致，不能确认成功。')}
 const complete=await request(api+'/complete',{context,events},session)
 return {...complete,context,events}
}
export function download(name:string,value:unknown){const blob=new Blob([typeof value==='string'?value:JSON.stringify(value,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
export const outcomeText:Record<string,string>={accepted:'操作完成',policy_rejected:'当前要求未通过，请换一个更难猜的口令',authentication_failed:'认证未通过',account_exists:'账户已存在',stale_policy:'策略已更新，请刷新后再试',stale_or_missing_account:'账户状态已更新或账户不存在',account_disabled:'账户已停用',recovery_failed:'恢复凭证无效',privacy_budget_exhausted:'隐私预算已用完',published:'受保护画像已发布',cached:'使用已发布画像，没有重复消耗预算',policy_applied:'策略已应用',plan_assigned:'整改任务已分配',plan_stopped:'计划已停止',account_updated:'账户状态已更新',invalid_operation:'请求参数或秘密输入无效'}
