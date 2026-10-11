"""Strict X.509 certificates for the local-only installation."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import ipaddress,shutil
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID

def renew(runtime):
 runtime=Path(runtime);tls=runtime/'tls';tls.mkdir(exist_ok=True);now=datetime.now(timezone.utc)
 ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048);ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'ZipfGuard LOCAL demonstration CA')])
 ca=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
  .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
  .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),False)
  .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,None,None),True).sign(ca_key,hashes.SHA256()))
 (tls/'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
 for name in ['user','admin']+[f'party_{i}' for i in range(3)]:
  folder=runtime/name;identity=f'MPyC party {name[-1]}' if name.startswith('party') else name
  key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
  cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,identity)])).issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
   .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
   .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),False)
   .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,None,None),True)
   .add_extension(x509.SubjectAlternativeName([x509.DNSName(identity),x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH,ExtendedKeyUsageOID.SERVER_AUTH]),False).sign(ca_key,hashes.SHA256()))
  (folder/'tls.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM));(folder/'tls.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
  if name.startswith('party'):
   config=folder/'.config';config.mkdir(exist_ok=True);shutil.copyfile(folder/'tls.crt',config/(name+'.crt'));shutil.copyfile(folder/'tls.key',config/(name+'.key'));shutil.copyfile(tls/'ca.crt',config/'mpyc_ca.crt')
