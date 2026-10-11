"""Closed schemas: ordinary business services cannot accept password inputs."""
from __future__ import annotations

import re
import math
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

USER_OPS = {'check', 'register', 'migrate', 'login', 'change', 'recover', 'delete'}
ADMIN_OPS = {'profile', 'apply_policy', 'rollback_policy', 'assign_plan', 'stop_plan', 'set_account', 'cancel_job'}
MUTATING_OPS = {'register', 'change', 'recover', 'delete', 'apply_policy', 'rollback_policy', 'assign_plan', 'stop_plan', 'set_account'}
FIELDS = {'check': {'new'}, 'register': {'new'}, 'migrate': {'new'}, 'login': {'candidate'}, 'change': {'old', 'new'}, 'recover': {'new'}, 'delete': {'candidate'}}


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Context(Closed):
    protocol: Literal['zipfguard-tool-v1'] = 'zipfguard-tool-v1'
    site: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    op: str = Field(min_length=1, max_length=32)
    account: str | None = Field(default=None, max_length=64)
    nonce: str = Field(min_length=24, max_length=96, pattern=r'^[a-zA-Z0-9_-]+$')
    policy_version: int = Field(ge=1)
    account_version: int = Field(default=0, ge=0)
    client_key: str = Field(pattern=r'^[0-9a-f]{64}$')
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def allowed_parameters(self):
        allowed = {'check': set(), 'register': {'recovery_key'}, 'login': set(),
                   'migrate': {'recovery_key','migration_ticket'},
                   'change': {'recovery_key'}, 'recover': {'recovery_key', 'recovery_signature'},
                   'delete': set(), 'profile': {'epsilon','stage'}, 'apply_policy': {'policy'},
                   'rollback_policy': {'version'}, 'assign_plan': {'snapshot', 'shapes', 'positions', 'quota', 'total_cap', 'fragments'},
                   'stop_plan': {'plan'}, 'set_account': {'enabled'}, 'cancel_job': {'job'}}
        if self.op not in allowed or set(self.params) - allowed[self.op]:
            raise ValueError('operation or parameter not allowed')
        if self.op in {'register', 'migrate', 'recover'} and not re.fullmatch(r'[0-9a-f]{64}', self.params.get('recovery_key', '')):
            raise ValueError('recovery public key required')
        if self.op in {'register', 'migrate', 'login', 'change', 'recover', 'delete', 'set_account'} and not self.account:
            raise ValueError('account required')
        p=self.params
        if self.op=='migrate' and (not isinstance(p.get('migration_ticket'),str) or len(p['migration_ticket'])>10000):raise ValueError('migration authorization required')
        if self.op=='profile':
            epsilon=p.get('epsilon',1)
            if isinstance(epsilon,bool) or not isinstance(epsilon,(int,float)) or not math.isfinite(epsilon) or not .1<=epsilon<=5:raise ValueError('invalid epsilon')
            if p.get('stage','analysis') not in {'analysis','confirmation'}:raise ValueError('invalid release stage')
        if self.op=='apply_policy':Policy.model_validate(p.get('policy'))
        if self.op=='rollback_policy' and (type(p.get('version')) is not int or p['version']<1):raise ValueError('invalid version')
        if self.op=='assign_plan':
            if any(type(p.get(k)) is not int or not 1<=p[k]<=10000 for k in ('quota','total_cap')):raise ValueError('invalid quota')
            Policy(snapshot=p.get('snapshot'),shapes=p.get('shapes',[]),positions=p.get('positions',[]),fragments=p.get('fragments',[]))
        if self.op=='set_account' and type(p.get('enabled')) is not bool:raise ValueError('invalid account status')
        if self.op in {'stop_plan','cancel_job'} and not re.fullmatch(r'[a-zA-Z0-9_-]{24,96}',p.get('plan' if self.op=='stop_plan' else 'job','')):raise ValueError('invalid job identifier')
        if self.op=='recover' and 'recovery_signature' in p and not re.fullmatch(r'[0-9a-f]{128}',p['recovery_signature']):raise ValueError('invalid recovery signature')
        return self

    @field_validator('account')
    @classmethod
    def identifier(cls, value):
        if value is not None and not re.fullmatch(r'[a-zA-Z0-9_.@-]{1,64}', value):
            raise ValueError('invalid account identifier')
        return value


class TicketRequest(Closed):
    context: Context


class Envelope(Closed):
    context: Context
    ticket: str = Field(max_length=10000)
    fields: dict[str, list[str]] = Field(default_factory=dict)
    salt: str = Field(pattern=r'^[0-9a-f]{64}$')
    signature: str = Field(pattern=r'^[0-9a-f]{128}$')


class ReceiptRequest(Closed):
    events: list[dict[str, Any]] = Field(min_length=3, max_length=3)
    context: Context


class AdminAuth(Closed):
    identifier: str = Field(min_length=1, max_length=80)
    invitation: str | None = Field(default=None, max_length=128)


class CredentialResponse(Closed):
    challenge_id: str = Field(max_length=96)
    credential: dict[str, Any]


class Reference(Closed):
    name: str
    source: str
    version: str
    words: list[str]
    dictionary: list[str]
    predictable_terms: list[str]


class Policy(Closed):
    minimum: int = Field(default=8, ge=8, le=64)
    maximum: int = Field(default=64, ge=8, le=64)
    k: int = Field(default=0, ge=0, le=30)
    j: int = Field(default=0, ge=0, le=8)
    fragments: list[int] = Field(default_factory=list, max_length=18)
    terms: list[str] = Field(default_factory=list, max_length=16)
    shapes: list[int] = Field(default_factory=list, max_length=8)
    positions: list[list[int]] = Field(default_factory=list, max_length=30)
    snapshot: str | None = None
    label: str = Field(default='基础策略', max_length=80)

    @field_validator('fragments')
    @classmethod
    def fragment_ids(cls, values):
        if len(set(values)) != len(values) or any(v not in range(1, 19) for v in values):
            raise ValueError('unknown or duplicate policy fragment')
        return values

    @model_validator(mode='after')
    def bounds(self):
        if self.minimum > self.maximum:
            raise ValueError('minimum exceeds maximum')
        if (self.shapes or self.positions or 17 in self.fragments) and not self.snapshot:
            raise ValueError('hidden rules require a frozen snapshot')
        if any(not re.fullmatch(r'[a-z0-9]{1,24}', term) for term in self.terms):
            raise ValueError('terms must be public lowercase ASCII words')
        if any(type(s) is not int or not 0<=s<8 for s in self.shapes):raise ValueError('invalid fixed structure position')
        if any(len(pair)!=2 or any(type(v) is not int for v in pair) or not 0<=pair[0]<=8 or not 0<=pair[1]<3 for pair in self.positions):raise ValueError('invalid anonymous rank')
        return self
