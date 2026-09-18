"""Durable discovery, separate from confirmation and acquisition authorization."""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone

from .contracts import PlanRequest, canonical, digest, now
from .store import Store
from .transport import Cancelled


ACTIVE=('pending','running','cancelling')


def get(store,identity):
    with store.connect() as c:
        row=c.execute('SELECT payload FROM discoveries WHERE id=?',(identity,)).fetchone()
    if row is None:raise KeyError('Unknown discovery job')
    return json.loads(row[0])


def update(store,identity,changes,event,expected=None):
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT payload FROM discoveries WHERE id=?',(identity,)).fetchone()
        if row is None:raise KeyError('Unknown discovery job')
        value=json.loads(row[0])
        if expected is not None and value['status'] not in expected:
            if value['status']=='cancelling':raise Cancelled('Discovery cancelled during stage transition')
            raise ValueError('Discovery state changed before '+event)
        value.update(changes);value['updated_at']=now()
        c.execute('UPDATE discoveries SET state=?,payload=? WHERE id=?',(value['status'],canonical(value).decode(),identity))
        store._event(c,identity,event,changes)
    return value


def submit(store,project,request,key):
    from .planning import context, implementation_hash
    if not key or not 8<=len(key)<=128:raise ValueError('Background discovery requires an 8–128 character Idempotency-Key header')
    supplied_hash=digest(request.model_dump(mode='json'))
    ctx,aoi=context(project)
    frozen_request=request.model_copy(update={'as_of':request.as_of or datetime.now(timezone.utc)})
    value={'id':uuid.uuid4().hex,'project':project,'status':'pending','stage':'queued','product':None,'submitted_at':now(),'updated_at':now(),
        'request':frozen_request.model_dump(mode='json'),'aoi_hash':digest(aoi),'target_epsg':ctx.target_epsg,'aoi_provenance':getattr(ctx,'aoi_provenance',None),
        'implementation':implementation_hash(),'plan_id':None,'plan_hash':None,'error':None}
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        previous=c.execute('SELECT payload,request_hash FROM discoveries WHERE project=? AND idempotency=?',(project,key)).fetchone()
        if previous:
            if previous[1]!=supplied_hash:raise ValueError('Discovery idempotency key was reused for different selections')
            return json.loads(previous[0])
        c.execute('INSERT INTO discoveries VALUES(?,?,?,?,?,?)',(value['id'],project,value['status'],canonical(value).decode(),key,supplied_hash))
        store._event(c,value['id'],'discovery_submitted',{'request':value['request'],'aoi_hash':value['aoi_hash'],'implementation':value['implementation']})
    return value


def active(store):
    with store.connect() as c:
        return [json.loads(row[0]) for row in c.execute("SELECT payload FROM discoveries WHERE state IN ('pending','running','cancelling') ORDER BY rowid")]


def execute(store,identity):
    from .planning import context, implementation_hash, build_plan
    value=get(store,identity)
    if value['status']=='cancelling':
        update(store,identity,{'status':'cancelled','stage':'cancelled'},'discovery_cancelled',ACTIVE)
        return
    try:
        ctx,aoi=context(value['project'])
        if digest(aoi)!=value['aoi_hash'] or ctx.target_epsg!=value['target_epsg'] or getattr(ctx,'aoi_provenance',None)!=value['aoi_provenance']:
            raise ValueError('Project AOI inputs or CRS changed while discovery was queued')
        if implementation_hash()!=value['implementation']:
            raise ValueError('Implementation changed while discovery was queued; submit a new discovery')
        update(store,identity,{'status':'running','stage':'starting'},'discovery_started',('pending','running'))
        def progress(stage,product):
            if get(store,identity)['status']=='cancelling':raise Cancelled('Discovery cancelled')
            update(store,identity,{'stage':stage,'product':product},'discovery_stage',('running',))
        plan=build_plan(value['project'],PlanRequest.model_validate(value['request']),store,
            cancelled=lambda:get(store,identity)['status']=='cancelling',progress=progress)
        # Serialize finalization against cancellation. A saved, unconfirmed plan
        # can exist after cancellation but cannot authorize an acquisition.
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=json.loads(c.execute('SELECT payload FROM discoveries WHERE id=?',(identity,)).fetchone()[0])
            cancelled=current['status']=='cancelling'
            current.update({'status':'cancelled' if cancelled else 'succeeded','stage':'cancelled' if cancelled else 'ready_for_review','updated_at':now(),
                'plan_id':None if cancelled else plan.plan_id,'plan_hash':None if cancelled else plan.plan_hash})
            c.execute('UPDATE discoveries SET state=?,payload=? WHERE id=?',(current['status'],canonical(current).decode(),identity))
            store._event(c,identity,'discovery_finished',{'status':current['status'],'plan_id':current['plan_id'],'plan_hash':current['plan_hash']})
    except Cancelled as exc:
        update(store,identity,{'status':'cancelled','stage':'cancelled','error':str(exc)},'discovery_cancelled',ACTIVE)
    except Exception as exc:
        # A failure to persist this result propagates and stops the worker.
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=json.loads(c.execute('SELECT payload FROM discoveries WHERE id=?',(identity,)).fetchone()[0])
            if current['status'] not in ACTIVE:raise
            state='cancelled' if current['status']=='cancelling' else 'failed'
            current.update({'status':state,'stage':state,'error':str(exc),'updated_at':now()})
            c.execute('UPDATE discoveries SET state=?,payload=? WHERE id=?',(state,canonical(current).decode(),identity))
            store._event(c,identity,'discovery_'+state,{'error':str(exc)})


def recover(store):
    for value in active(store):
        if value['status']=='running':
            update(store,value['id'],{'status':'pending','stage':'queued_after_restart'},'discovery_restart',('running',))
