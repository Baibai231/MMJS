"""Provision an explicitly local three-process installation, never a production CA."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import argparse,base64,hashlib,ipaddress,json,secrets,shutil
from datetime import datetime,timedelta,timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
from services.common.security import create_private,public_hex
from services.common.store import Store

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def provision(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    if (directory/'installation.json').exists():return json.loads((directory/'installation.json').read_text('utf-8'))
    tls=directory/'tls';tls.mkdir()
    now=datetime.now(timezone.utc);ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'ZipfGuard LOCAL demonstration CA')])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,encipher_only=None,decipher_only=None),critical=True)
        .sign(ca_key,hashes.SHA256()))
    (tls/'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    def certificate(identity,folder):
        folder.mkdir(parents=True,exist_ok=True)
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,identity)]))
              .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
              .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False)
              .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=True,data_encipherment=False,key_agreement=False,key_cert_sign=False,crl_sign=False,encipher_only=None,decipher_only=None),critical=True)
              .add_extension(x509.SubjectAlternativeName([x509.DNSName(identity),x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
              .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH,ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
              .sign(ca_key,hashes.SHA256()))
        (folder/'tls.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (folder/'tls.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    extension_key=rsa.generate_private_key(public_exponent=65537,key_size=2048).public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
    extension_key_b64=base64.b64encode(extension_key).decode()
    extension_id=''.join(chr(ord('a')+int(n,16)) for n in hashlib.sha256(extension_key).hexdigest()[:32])
    user_origin='chrome-extension://'+extension_id;admin_origin='https://localhost:8532'
    keys={};node_keys=[]
    for service in ['user','admin']:
        folder=directory/service;folder.mkdir()
        keys[service]=public_hex(create_private(folder/'issuer.key'));certificate(service,folder)
    for i in range(3):
        folder=directory/f'party_{i}';folder.mkdir()
        node_keys.append(public_hex(create_private(folder/'receipt.key')))
        (folder/'binding.key').write_bytes(secrets.token_bytes(32));certificate(f'MPyC party {i}',folder)
        configuration=folder/'.config';configuration.mkdir()
        shutil.copyfile(folder/'tls.crt',configuration/f'party_{i}.crt')
        shutil.copyfile(folder/'tls.key',configuration/f'party_{i}.key')
        shutil.copyfile(tls/'ca.crt',configuration/'mpyc_ca.crt')
    nodes=[f'https://localhost:{8541+i}' for i in range(3)]
    common={'site':'demo','name':'ZipfGuard 研究站点','nodes':nodes,'node_keys':node_keys,'epsilon_cap':10,'task_cap':100,
            'user_api':'https://localhost:8531','admin_api':admin_origin,'reference_site':'https://localhost:8560',
            'ca':str(tls/'ca.crt'),'local_demo':True}
    metadata=str(directory/'business.sqlite')
    for service,port,origin in [('user',8531,user_origin),('admin',8532,admin_origin)]:
        folder=directory/service
        write(folder/'config.json',{**common,'kind':service,'port':port,'store':metadata,'key':str(folder/'issuer.key'),
             'cert':str(folder/'tls.crt'),'tls_key':str(folder/'tls.key'),'origins':[origin],
             'rp_id':'localhost','web_origin':admin_origin})
    for i in range(3):
        folder=directory/f'party_{i}'
        write(folder/'config.json',{**common,'port':8541+i,'cert':str(folder/'tls.crt'),'tls_key':str(folder/'tls.key'),
              'user_issuer':keys['user'],'admin_issuer':keys['admin'],'allowed_origins':[user_origin,admin_origin],
              'source_limit':1000,'site_limit':500,'account_limit':30})
    invitation=secrets.token_urlsafe(32)
    Store(metadata).put('invitations',invitation,{'identifier':'*','role':'owner','expires':now.timestamp()+86400})
    (directory/'administrator-installation-code.txt').write_text(invitation,encoding='utf-8')
    # Only public trust material is shipped to the extension. This is provisioned
    # independently, never replaced from an administrator-controlled page.
    trust={**common,'user_issuer':keys['user'],'extension_id':extension_id}
    trust.pop('ca')
    write(ROOT/'apps/user-assistant/public/trusted-stations.json',[trust])
    write(ROOT/'apps/user-assistant/public/manifest.json',{
        'manifest_version':3,'name':'ZipfGuard 用户口令助手','version':'1.0.0','key':extension_key_b64,
        'description':'在独立扩展中私密注册、登录与修改口令。网站不接收口令明文。',
        'action':{'default_popup':'popup.html'},'permissions':['storage'],
        'host_permissions':[url+'/*' for url in nodes+['https://localhost:8531']],
        'content_security_policy':{'extension_pages':"script-src 'self'; object-src 'self'"}})
    result={**common,'runtime':str(directory),'extension_id':extension_id,'mpc_base_port':8551,'user_origin':user_origin}
    write(directory/'installation.json',result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',default=str(ROOT/'tool_runtime'));args=parser.parse_args()
    cfg=provision(args.runtime)
    print('Local installation provisioned. Administrator installation code remains in the private runtime directory.')
    print('User extension identity:',cfg['extension_id'])
