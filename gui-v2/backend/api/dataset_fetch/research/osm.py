"""Preserve relation trees as ordered member geometries, without polygon inference."""
from __future__ import annotations
import json
import math


def osm_features(payload,root_ids=None):
    elements={}
    for element in payload['elements']:
        key=(element.get('type'),element.get('id'))
        if key[0] not in ('node','way','relation') or type(key[1]) is not int or key[1]<=0 or key in elements:
            raise ValueError('OSM identities are invalid or duplicated')
        elements[key]=element
        if root_ids is not None and (type(element.get('version')) is not int or element['version']<1 or not isinstance(element.get('timestamp'),str)):
            raise ValueError('OSM snapshot element lacks mandatory version or timestamp metadata')
    roots=root_ids if root_ids is not None else [{'type':kind,'id':id} for kind,id in sorted(elements) if kind!='node']
    for root in roots:
        element=elements.get((root['type'],root['id']))
        if not element:
            raise ValueError('OSM response is missing a frozen root identity')
        for field in ('version','timestamp'):
            if field in root and element.get(field)!=root[field]:
                raise ValueError('OSM root changed after its snapshot was frozen')
    features=[]
    def point(value):
        if not isinstance(value,dict) or not all(type(value.get(k)) in (int,float) and math.isfinite(value[k]) for k in ('lon','lat')) or abs(value['lon'])>180 or abs(value['lat'])>90:
            raise ValueError('OSM geometry is incomplete or outside longitude/latitude bounds')
        return [value['lon'],value['lat']]
    def geometry(element):
        if element['type']=='node': return {'type':'Point','coordinates':point(element.get('geometry',element))}
        values=element.get('geometry')
        if root_ids is not None:
            nodes=element.get('nodes')
            if not isinstance(nodes,list) or len(nodes)<2 or any(('node',id) not in elements for id in nodes):
                raise ValueError('OSM way has missing mandatory node dependencies')
            node_coordinates=[point(elements[('node',id)]) for id in nodes]
            if values is not None and [point(value) for value in values]!=node_coordinates:
                raise ValueError('OSM way geometry contradicts its node dependencies')
        if values is None:
            values=[elements.get(('node',id),{}) for id in element.get('nodes',[])]
        if not isinstance(values,list) or len(values)<2:
            raise ValueError('OSM geometry is incomplete')
        return {'type':'LineString','coordinates':[point(value) for value in values]}
    def walk(root,element,path,ancestry,stack):
        identity=(element['type'],element.get('id',element.get('ref')))
        if identity in stack or len(stack)>64:
            raise ValueError('OSM nested relation is cyclic or exceeds the depth limit')
        if element['type']=='relation':
            members=element.get('members')
            if not isinstance(members,list) or not members:
                raise ValueError('OSM relation has no supported members')
            for index,member in enumerate(members):
                if member.get('type') not in ('node','way','relation'):
                    raise ValueError('OSM relation has an unsupported member type')
                child=elements.get((member['type'],member.get('ref')))
                if child is None:
                    if member['type']=='relation' or root_ids is not None:
                        raise ValueError('OSM nested relation or mandatory descendant is unresolved')
                    child=member
                relation={'relation_id':identity[1],'relation_version':element.get('version'),'member_index':index,'member_type':member['type'],'member_ref':member.get('ref'),'role':member.get('role','')}
                walk(root,child,path+[index],ancestry+[relation],stack|{identity})
            return
        key=f"{root['type']}/{root['id']}"+('/member/'+'/'.join(map(str,path)) if path else '')
        member=ancestry[-1] if ancestry else {}
        properties={'source_id':key,'osm_type':root['type'],'osm_id':str(root['id']),'osm_version':root.get('version'),'osm_timestamp':root.get('timestamp'),
            'tags_json':json.dumps(root.get('tags',{}),sort_keys=True),'member_role':member.get('role'),'member_ref':str(member.get('member_ref','')),
            'member_type':element['type'],'member_index':path[-1] if path else None,'member_path':path,'relation_ancestry':ancestry,
            'leaf_osm_id':identity[1],'leaf_osm_version':element.get('version'),'leaf_osm_timestamp':element.get('timestamp'),'leaf_tags':element.get('tags',{})}
        properties.update({k:v for k,v in root.get('tags',{}).items() if k not in properties})
        features.append({'type':'Feature','id':key,'properties':properties,'geometry':geometry(element)})
        if len(features)>1_000_000: raise ValueError('OSM member geometry exceeds the feature limit')
    for root in sorted(roots,key=lambda r:(r['type'],r['id'])):
        element=elements[(root['type'],root['id'])]
        walk(element,element,[],[],set())
    return features
