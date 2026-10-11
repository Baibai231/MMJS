"""Website-side receipt verification; no password or upload relay API."""
from services.common.security import receipt_quorum,verify_token,token

def authenticate(events,node_public_keys,site,nonce,context_digest):
    result=receipt_quorum(events,node_public_keys,{'site':site,'nonce':nonce,'context_digest':context_digest})
    if result['op'] not in {'login','register','migrate','change','recover'} or result['outcome']!='accepted':
        raise ValueError('not an authentication result')
    return {'account':result['account'],'site':site,'nonce':nonce,'version':result['result']['account_version']}

def migration_authorization(private_key,site,authenticated_account):
    """Call ONLY after validating an existing site's authorized user session.

    This token cannot overwrite an already enrolled private account; every node
    verifies the site issuer and rejects any existing distributed credential.
    """
    return token(private_key,'user-migration',{'site':site,'sub':authenticated_account},300)
