import{request}from'../../shared/protocol';
const decode=(value:string)=>Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
const encode=(value:ArrayBuffer)=>btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
export async function passkey(mode:'enroll'|'login',identifier:string,invitation?:string){
 const challenge=await request('/auth/options/'+mode,{identifier,invitation:invitation||null});const options=challenge.options;
 options.challenge=decode(options.challenge);if(options.user)options.user.id=decode(options.user.id);
 for(const key of ['allowCredentials','excludeCredentials'])if(options[key])options[key]=options[key].map((c:any)=>({...c,id:decode(c.id)}));
 const credential=(await(mode==='enroll'?navigator.credentials.create({publicKey:options}):navigator.credentials.get({publicKey:options}))) as PublicKeyCredential;
 if(!credential)throw Error('通行密钥操作已取消');const response=credential.response;
 let details:any={clientDataJSON:encode(response.clientDataJSON)};
 if(response instanceof AuthenticatorAttestationResponse)details={...details,attestationObject:encode(response.attestationObject),transports:response.getTransports()};
 else {const auth=response as AuthenticatorAssertionResponse;details={...details,authenticatorData:encode(auth.authenticatorData),signature:encode(auth.signature),userHandle:auth.userHandle?encode(auth.userHandle):null}}
 return request('/auth/verify',{challenge_id:challenge.challenge_id,credential:{id:credential.id,rawId:encode(credential.rawId),type:credential.type,response:details,clientExtensionResults:credential.getClientExtensionResults()}})
}
