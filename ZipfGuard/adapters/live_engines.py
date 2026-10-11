"""Numeric adapters: no live plaintext population is passed to research engines."""
from __future__ import annotations
import numpy as np
from policy.privacy_joint_profile import choose_shapes,choose_positions,predict_conditional
from policy.intervention_fragments import LABELS


def registration_candidates(profile,base,k=3,j=1,cost_cap=0.0,pending_cap=0):
    counts=np.asarray(profile['counts']); spectra=np.asarray(profile['spectra'])
    numeric={'counts':counts,'spectra':spectra}
    result=[]
    for exact,shape in [(0,0),(k,0),(0,j),(k,j)]:
        shapes=choose_shapes(counts,int(shape),len(counts)-1)
        positions=choose_positions(numeric,int(exact),shapes,residual=True)
        coverage=predict_conditional(numeric,positions,shapes)
        policy={**base,'k':int(exact),'j':int(shape),'positions':[list(v) for v in positions],
                'shapes':list(shapes),'snapshot':profile['snapshot'] if positions or shapes else None}
        if exact==0 and shape==0:policy=dict(base)
        active=bool(positions or shapes)
        # Coverage is identifiable from the approved DP profile. Future reselection,
        # attack-rank improvement and unfinished cost are NOT identifiable without
        # a response model and independent confirmation; fail closed on that gate.
        result.append({'policy':policy,'coverage_estimate':coverage,'coverage_fraction':coverage/max(1,profile['population']),
                       'risk_effect':None,'reselection_cost':None,'pending_estimate':None,
                       'eligible':not active,'decision':'hold' if active else 'base',
                       'reason':'尚无本站用户响应模型及独立确认，无法证明重选成本与未完成人数满足门槛' if active else '保持当前基础要求',
                       'cost_cap':cost_cap,'pending_cap':pending_cap})
    return result


def intervention_candidates(profile,quota,total_cap,used):
    rules=profile['fragment_violations']
    return [{'fragment':n,'label':LABELS[n],'violation_estimate':rules[n-1],
             'assignable_limit':min(int(quota),max(0,int(total_cap)-int(used))),
             'effect_estimate':None,'source':'DP规则违反人数估计；实际资格由三方确认',
             'uncertainty':'统计噪声较大时不能可靠排序；未来改密效果须用真实反馈检验'} for n in range(1,19)]
