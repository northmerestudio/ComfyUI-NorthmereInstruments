import json
import math
import pytest
from northmere_instruments.envelope import DEFAULT_CURVE, LoRAEnvelope, sigma_to_progress


def test_default_exact_values():
    curve = LoRAEnvelope.from_json(DEFAULT_CURVE)
    assert [curve.at(x) for x in curve.xs] == pytest.approx([.5,.8,.92,.968,.9872,.99488,.997952,.9991808,1.0])


def test_linear_step_independent():
    curve=LoRAEnvelope.from_json({'version':1,'interpolation':'linear','points':[[0,-1],[.5,2],[1,0]]})
    assert curve.at(.25)==.5
    assert curve.at(.75)==1
    assert curve.at(-1)==-1
    assert curve.at(2)==0


def test_hold():
    curve=LoRAEnvelope.from_json({'version':1,'interpolation':'hold','points':[[0,.5],[.25,1.5],[1,-.5]]})
    assert curve.at(.249999)==.5
    assert curve.at(.25)==1.5
    assert curve.at(.99)==1.5
    assert curve.at(1)==-.5


def test_smooth_monotonic_no_overshoot():
    curve=LoRAEnvelope.from_json({'version':1,'interpolation':'smooth','points':[[0,.5],[.2,.8],[.3,.92],[.5,.95],[1,1]]})
    values=[curve.at(i/1000) for i in range(1001)]
    assert all(a<=b+1e-12 for a,b in zip(values,values[1:]))
    assert values[0]==.5 and values[-1]==1


def test_smooth_extrema_no_overshoot():
    curve=LoRAEnvelope.from_json({'version':1,'interpolation':'smooth','points':[[0,0],[.4,2],[.6,-1],[1,0]]})
    assert all(-1<=curve.at(i/500)<=2 for i in range(501))

@pytest.mark.parametrize('bad',[
    'not json','{}',json.dumps({'version':2,'points':[[0,0],[1,1]]}),
    json.dumps({'version':1,'points':[[.1,0],[1,1]]}),
    json.dumps({'version':1,'points':[[0,0],[0,1],[1,1]]}),
    json.dumps({'version':1,'points':[[0,0],[1,21]]}),
    json.dumps({'version':1,'points':[[0,0],[1,float('nan')]]}),
    json.dumps({'version':1,'points':[[0,False],[1,1]]}),
])
def test_reject_invalid(bad):
    with pytest.raises(ValueError): LoRAEnvelope.from_json(bad)


def test_sigma_inverse():
    class Sampling:
        def percent_to_sigma(self, p): return 15*(1-p)
    s=Sampling()
    for p in [0,.01,.25,.75,.99,1.]:
        assert sigma_to_progress(s,s.percent_to_sigma(p))==pytest.approx(p,abs=1e-8)
