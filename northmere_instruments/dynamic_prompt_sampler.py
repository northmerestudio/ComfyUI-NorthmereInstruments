"""Northmere prompt language: parsed AST, exact small-state sampling, bounded large-state search.

Approximate search enforces hard rules but does not promise the exact target law.
"""
from __future__ import annotations

import logging
import math
import random
import re
from fractions import Fraction

from . import dynamic_prompt_parser as language

MAX_NODES = 30000
MAX_RENDERED = 1_000_000
MAX_SEARCH = 200_000
EXACT_BUDGET = 12000

class PromptLanguageError(ValueError):
    pass


def _expand_refs(nodes, templates, count, stack=()):
    result=[]
    for node in nodes:
        count[0]+=1
        if count[0]>MAX_NODES: raise PromptLanguageError('Expanded AST exceeds node budget')
        if isinstance(node,language.Ref):
            if node.name in stack: raise PromptLanguageError('Cyclic template reference')
            result.extend(_expand_refs(templates[node.name],templates,count,stack+(node.name,)))
        elif isinstance(node,language.Choice):
            result.append(language.Choice(tuple(language.Branch(_expand_refs(b.children,templates,count,stack),b.facts,b.weight) for b in node.branches)))
        elif isinstance(node,language.Conditional):
            result.append(language.Conditional(node.condition,_expand_refs(node.children,templates,count,stack)))
        else:result.append(node)
    return tuple(result)


def _possible(nodes):
    facts=set()
    for node in nodes:
        if isinstance(node,language.Choice):
            for b in node.branches:
                facts.update(b.facts)
                facts.update(_possible(b.children))
    return facts


def _render(nodes, facts, rng, count):
    out=[]
    for node in nodes:
        if isinstance(node,language.Text):
            out.append(node.value)
            count[0] += len(node.value)
        elif isinstance(node,language.Conditional):
            if node.condition in facts:out.append(_render(node.children,facts,rng,count))
        elif isinstance(node,language.Choice):
            choice=rng.choices(node.branches,weights=[float(b.weight) for b in node.branches],k=1)[0]
            out.append(_render(choice.children,facts,rng,count))
        else:raise AssertionError(type(node))
        if count[0]>MAX_RENDERED:raise PromptLanguageError('Rendered prompt exceeds size limit')
    return ''.join(out)


def _expr_names(expr):
    if expr[0]=='name':return {expr[1]}
    if expr[0]=='not':return _expr_names(expr[1])
    return _expr_names(expr[1])|_expr_names(expr[2])


def _tri(expr, present, possible):
    """False/True/None for definitely false/true/as-yet-undecided."""
    typ=expr[0]
    if typ=='name':return True if expr[1] in present else None if expr[1] in possible else False
    if typ=='not':
        value=_tri(expr[1],present,possible)
        return None if value is None else not value
    a=_tri(expr[1],present,possible);b=_tri(expr[2],present,possible)
    if typ=='and':return False if a is False or b is False else True if a is True and b is True else None
    if typ=='or':return True if a is True or b is True else False if a is False and b is False else None
    raise AssertionError(typ)


def _bounded_sample(p, seed):
    rng=random.Random(seed)
    tree=_expand_refs(p.body,p.templates,[0])
    decisions=[]
    for name,variants in p.decisions.items():
        branches=[language.Branch((),frozenset({name+'.'+v}),w) for v,w in variants]
        decisions.append(language.Choice(tuple(branches)))
    tree=tuple(decisions)+tree
    drivers=set().union(*(_expr_names(a)|_expr_names(b) for a,b in p.requirements)) if p.requirements else set()
    pref_names=set().union(*(_expr_names(a) for a,_ in p.preferences)) if p.preferences else set()
    count=[0]
    
    def search(nodes,selected):
        count[0]+=1
        if count[0]>MAX_SEARCH:raise PromptLanguageError('Bounded constraint search exhausted; simplify rules or choices')
        possible=_possible(nodes)
        for a,b in p.requirements:
            lhs=_tri(a,selected,possible);rhs=_tri(b,selected,possible)
            if lhs is True and rhs is False:return None
        candidates=[]
        for i,node in enumerate(nodes):
            if isinstance(node,language.Choice):
                tags=_possible((node,))
                if tags:
                    priority=3 if tags & pref_names else 2 if tags & drivers else 1
                    candidates.append((priority,i,node))
        if not candidates:
            if all(not language.eval_expr(a,selected) or language.eval_expr(b,selected) for a,b in p.requirements):
                return nodes,selected
            return None
        _,index,choice=max(candidates,key=lambda t:t[0])
        remaining=list(choice.branches)
        while remaining:
            weights=[]
            for branch in remaining:
                after=selected|branch.facts
                w=float(branch.weight)
                for predicate,factor in p.preferences:
                    if language.eval_expr(predicate,after) and not language.eval_expr(predicate,selected):w*=float(factor)
                if not math.isfinite(w) or w<=0:raise PromptLanguageError('Preference weights overflow')
                weights.append(w)
            idx=rng.choices(range(len(remaining)),weights=weights,k=1)[0]
            branch=remaining.pop(idx)
            proposal=nodes[:index]+branch.children+nodes[index+1:]
            answer=search(proposal,selected|branch.facts)
            if answer is not None:return answer
        return None
    resolved=search(tree,frozenset())
    if resolved is None:raise PromptLanguageError('No rule-consistent selection found')
    nodes,facts=resolved
    return _render(nodes,facts,rng,[0]), {'mode':'approximate', 'facts':sorted(facts),'attempts':count[0]}


def expand_prompt(source: str, seed: int=0, *, mode: str='auto', with_trace: bool=False):
    if not isinstance(source,str):raise PromptLanguageError('Prompt must be text')
    if not isinstance(seed,int) or isinstance(seed,bool) or seed<0 or seed>=2**64:
        raise PromptLanguageError('Seed must be an unsigned 64-bit integer')
    if mode not in ('auto','exact','approximate'):raise PromptLanguageError('Invalid sampling mode')
    # Refuse unescaped v3 directives instead of misrendering them as literal text.
    if re.search(r'(?<!\\)@(?:pick|if|use|define|rules|end)\b',source):
        raise PromptLanguageError('Legacy v3 @directive syntax is unsupported; convert this template to the current syntax')
    try:p=language.parse(source)
    except language.LanguageError as e:raise PromptLanguageError(str(e)) from e
    if mode!='approximate':
        try:
            outcomes=language.enumerate_outcomes(p,max_states=EXACT_BUDGET)
            # Portable exact reference uses signed 64-bit seeds.
            if seed<2**63:
                output,facts=language.exact_sample(p,seed,outcomes)
            else:
                # Deterministic unsigned-to-signed mapping, documented node behavior.
                output,facts=language.exact_sample(p,seed-2**64,outcomes)
            trace={'mode':'exact','facts':sorted(facts),'states':len(outcomes)}
            return (output,trace) if with_trace else output
        except language.LanguageError as e:
            if 'budget exceeded' not in str(e) or mode=='exact':raise PromptLanguageError(str(e)) from e
    result,trace=_bounded_sample(p,seed)
    logging.getLogger(__name__).debug('Northmere prompt used bounded approximate search: %s',trace)
    return (result,trace) if with_trace else result
