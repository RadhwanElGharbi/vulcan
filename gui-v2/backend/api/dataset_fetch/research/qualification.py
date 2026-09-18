"""Expose dated QA evidence without promoting a sample check to qualification."""
from __future__ import annotations
import json
from .contracts import digest
from .store import ROOT


def attach_verification(product):
    from .planning import implementation_hash
    path=ROOT/'qa/research-qualification-index.json'
    if path.exists():
        index=json.loads(path.read_text(encoding='utf-8'))
        entry=next((p for p in index['products'] if p['product']==product.id),None)
        attempt=entry.get('latest_acquisition_attempt') if entry else None
        if attempt:
            product.assessment.verification['latest_live_acquisition']={key:attempt[key] for key in ('status','checked_at','error','plan_hash','report_hash','implementation') if key in attempt}
            product.assessment.verification['latest_live_acquisition']['matches_current_implementation']=attempt.get('implementation')==implementation_hash()
            product.assessment.verification['latest_live_acquisition']['scope']='One preserved sample AOI and selected parameters, with two offline replays when passed; not full product qualification'
            product.assessment.verification['latest_live_acquisition']['evidence_record_hash']=digest(attempt)
    path=ROOT/'qa/research-provider-discovery.json'
    if path.exists():
        index=json.loads(path.read_text(encoding='utf-8'))
        attempt=next((p for p in index['products'] if p['product']==product.id),None)
        if attempt:
            product.assessment.verification['latest_discovery']={key:attempt[key] for key in ('status','checked_at','error') if key in attempt}
            product.assessment.verification['latest_discovery']['scope']='Service discovery only; not acquisition or scientific qualification'
    return product
