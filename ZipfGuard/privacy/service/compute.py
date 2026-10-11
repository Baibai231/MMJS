"""Secret-only ASCII policy checks and conditional frequency snapshots.

No function opens a password, class sequence, or individual frequency.
The fixed public shape catalogue and the OTHER bucket are versioned explicitly.
"""
from __future__ import annotations
import re
import secrets
from decimal import Decimal, localcontext, ROUND_CEILING

WIDTH = 64
SHAPES = ['L8', 'L10', 'D8', 'D10', 'L5D4S1', 'U1L5D3S1', 'L10D2', 'U1L8D2S1']
SHAPE_VERSION = 'ascii-luds-runs-v1-fixed-catalogue'


class Computer:
    def __init__(self, mpc, reference):
        self.mpc = mpc
        self.sec = mpc.SecInt(32)
        self.field = mpc.SecFld(order=self.sec.field.order, signed=True)
        self.reference = reference

    def all(self, values):
        return self.mpc.all(list(values))

    def any(self, values):
        return 1 - self.all(1-v for v in values)

    def equal(self, a, b):
        return self.all(x == y for x, y in zip(a, b))

    async def input(self, shares):
        distributed = self.mpc.input([self.field(int(v)) for v in shares])
        bits = [2*a-b for a, b in zip(distributed[0], distributed[1])]
        errors = [c-2*b+a for a, b, c in zip(*distributed)] + [b*(b-1) for b in bits]
        coefficients = self.mpc._randoms(self.field, len(errors))
        if not await self.mpc.is_zero_public(self.mpc.in_prod(errors, coefficients)):
            raise ValueError('inconsistent or nonbinary input')
        local = [self.sec(v.share) for v in bits]
        return [self.mpc.sum([local[8*i+k]*(1 << k) for k in range(8)]) for i in range(WIDTH)]

    def characterize(self, chars, include_shape=True):
        nz = [c != 0 for c in chars]
        low = [(c >= 97)*(c <= 122) for c in chars]
        up = [(c >= 65)*(c <= 90) for c in chars]
        digit = [(c >= 48)*(c <= 57) for c in chars]
        # Fragment symbol semantics excludes ASCII whitespace.
        symbol = [p-l-u-d-(c == 32) for p,l,u,d,c in zip(nz,low,up,digit,chars)]
        code = [l+2*u+3*d+4*(p-l-u-d) for p,l,u,d in zip(nz,low,up,digit)]
        valid = self.all([(c == 0)+((c >= 32)*(c <= 126)) for c in chars] +
                         [1-(1-nz[i])*nz[i+1] for i in range(WIDTH-1)])
        lowered = [c+32*u for c,u in zip(chars,up)]
        matches = []
        for shape in SHAPES if include_shape else []:
            pattern = [dict(L=1,U=2,D=3,S=4)[kind] for kind,n in re.findall(r'([LUDS])(\d+)',shape) for _ in range(int(n))]
            pattern += [0]*(WIDTH-len(pattern))
            matches.append(self.equal(code,pattern))
        shape = self.sec(len(SHAPES))-self.mpc.sum([v*(len(SHAPES)-i) for i,v in enumerate(matches)])
        return {'valid':valid, 'length':self.mpc.sum(nz), 'nz':nz, 'low':low, 'up':up,
                'digit':digit, 'symbol':symbol, 'lowered':lowered, 'shape':shape}

    def public_word(self, chars, word):
        raw = list(word.encode('ascii'))
        return self.equal(chars, raw+[0]*(WIDTH-len(raw))) if len(raw)<=WIDTH else self.sec(0)

    def contains(self, chars, word):
        raw = list(word.encode('ascii'))
        return self.any(self.equal(chars[i:i+len(raw)],raw) for i in range(WIDTH-len(raw)+1))

    def fragment(self, number, chars, meta, bank=()):
        m = self.mpc
        if number in (1,2,3): return meta['length'] >= {1:10,2:12,3:15}[number]
        if number == 4: return self.any(meta['low']+meta['up'])
        if number in (5,6,7): return self.any(meta[{5:'low',6:'up',7:'digit'}[number]])
        if number == 8: return self.any(meta['digit']+meta['symbol'])
        if number == 9: return m.sum([self.any(meta[k]) for k in ('low','up','digit','symbol')])>=2
        if number == 10:
            # Select three additional distinct representatives obliviously.
            # Secret find + selection costs O(4*width), rather than O(width²).
            representatives=[chars[0]];found=self.sec(1)
            for _ in range(3):
                eligible=[meta['nz'][i]*self.all(chars[i]!=r for r in representatives) for i in range(WIDTH)]
                found*=self.any(eligible)
                index=m.find(eligible,1,e=0)
                representatives.append(m.in_prod(chars,m.unit_vector(index,WIDTH)))
            return found
        if number == 11: return self.all(1-meta['nz'][i]*(chars[i]==chars[i+1])*(chars[i]==chars[i+2]) for i in range(WIDTH-2))
        if number == 12: return self.any(meta['nz'][i]*(chars[i]!=chars[0]) for i in range(1,WIDTH))
        if number == 13: return self.all(1-self.contains(meta['lowered'], w) for w in ('qwerty','asdf','zxcv','qazwsx','1qaz','2wsx'))
        if number == 14:
            hit = []
            for i in range(WIDTH-3):
                c = meta['lowered'][i:i+4]
                same_kind = self.all((x>=97)*(x<=122) for x in c)+self.all((x>=48)*(x<=57) for x in c)
                hit.append(same_kind*(self.all(c[j+1]-c[j]==1 for j in range(3))+self.all(c[j+1]-c[j]==-1 for j in range(3))))
            return self.all(1-v for v in hit)
        if number == 15: return self.all(1-self.public_word(meta['lowered'],w) for w in self.reference['dictionary'])
        if number == 16: return self.all(1-self.contains(meta['lowered'],w) for w in self.reference['predictable_terms'])
        if number == 17: return self.all(1-active*self.equal(chars,row) for row,active in bank)
        if number == 18: return self.all(1-self.public_word(chars,w) for w in self.reference['words'])
        raise ValueError('unknown fragment')

    def accept(self, chars, policy, bank=()):
        meta = self.characterize(chars,include_shape=bool(policy['shapes']))
        rules = [meta['valid'],meta['length']>=policy['minimum'],meta['length']<=policy['maximum']]
        rules += [self.fragment(n,chars,meta,bank) for n in policy['fragments']]
        rules += [1-self.contains(meta['lowered'],w) for w in policy['terms']]
        rules += [meta['shape']!=s for s in policy['shapes']]
        rules += [1-active*self.equal(chars,row) for row,active in bank] if policy['positions'] else []
        return self.all(rules)

    async def snapshot(self, rows, rank_limit=3):
        m=self.mpc
        n=len(rows)
        metas=[self.characterize(row) for row in rows]
        equality=[[self.equal(a,b) for b in rows] for a in rows]
        freq=[m.sum(line) for line in equality]
        unique=[self.sec(1) if i==0 else (m.sum(equality[i][:i])==0) for i in range(n)]
        counts=[]; spectra=[]; banks=[]
        for s in range(len(SHAPES)+1):
            selected=[meta['shape']==s for meta in metas]
            counts.append(m.sum(selected))
            # Secret index is never opened. A deterministic public account order
            # breaks ties consistently inside the oblivious comparator network.
            entries=[[freq[i]*unique[i]*selected[i]*(n+1)+(n-i),freq[i]*unique[i]*selected[i],*rows[i]] for i in range(n)]
            ordered=m.sorted(entries,key=lambda entry:entry[0],reverse=True)
            group=[]
            for r in range(rank_limit):
                item=ordered[r] if r<n else [self.sec(0)]*(WIDTH+2)
                spectra.append(item[1]); group.append((item[2:],item[1]>0))
            banks.append(group)
        return counts+spectra,banks

    async def noisy(self, vector, epsilon, sensitivity):
        # Each independent party supplies its own exact-rational discrete Laplace
        # noise; convolution preserves the bound if at least one stays private.
        denominator=1<<64
        with localcontext() as ctx:
            ctx.prec=100
            q=int(((-Decimal(str(epsilon))/Decimal(sensitivity)).exp()*denominator).to_integral_value(rounding=ROUND_CEILING))
        def geometric():
            value=0
            while secrets.randbelow(denominator)<q:
                value+=1
                if value >= 1<<20: raise OverflowError('bounded noise overflow: no release')
            return value
        own=[self.sec(geometric()-geometric()) for _ in vector]
        noise=m=self.mpc.input(own)
        combined=[vector[i]+self.mpc.sum([line[i] for line in noise]) for i in range(len(vector))]
        return [int(v) for v in await self.mpc.output(combined)]

    async def save_chars(self, chars):
        return [str(int(v.value)) for v in await self.mpc.gather(chars)]

    def load_chars(self, shares):
        return [self.sec(self.sec.field(int(v))) for v in shares]
