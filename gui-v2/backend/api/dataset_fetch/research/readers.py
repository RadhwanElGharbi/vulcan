from __future__ import annotations

import json
from pathlib import Path


def selection_label(name, parameters):
    labels=[]
    for key in ('area_type','year','country','depth','statistic','status','start','end','variables','acquisition_ids','scene_ids','bands'):
        value=parameters.get(key)
        if value is None:continue
        rendered=', '.join(map(str,value)) if isinstance(value,list) else str(value)
        if len(rendered)>100:rendered=str(len(value))+' selected values'
        rendered=rendered.replace('_',' ')
        if rendered not in name:labels.append(key.replace('_',' ')+': '+rendered)
    return name+(' · '+'; '.join(labels) if labels else '')


def active_datasets(project_path: Path):
    path = project_path / "data" / "active-generation.json"
    if not path.exists():
        return []
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "zeus.acquisition/2.0":
        raise ValueError("Unsupported generation manifest")
    generation=(project_path/'data/generations'/manifest['generation']).resolve()
    if not generation.is_relative_to((project_path/'data/generations').resolve()) or json.loads((generation/'manifest.json').read_text(encoding='utf-8'))!=manifest:
        raise ValueError('Active manifest differs from its immutable generation')
    for item in manifest["datasets"]:
        resolved = (project_path / item["path"]).resolve()
        if not resolved.is_relative_to(project_path.resolve()) or not resolved.is_file():
            raise ValueError("Active generation contains an invalid artifact path")
    return manifest["datasets"]


def resolve_active(project_path: Path, layer: str, kind: str):
    for item in active_datasets(project_path):
        if item["kind"] == kind and layer in (item["id"], item["name"]):
            from .contracts import file_hash
            if file_hash(project_path / item["path"]) != item["sha256"]:
                raise ValueError("Active scientific artifact failed its integrity check")
            return project_path / item["path"]
    return None


def companion_path(item, field):
    """Resolve generation-relative companions, including nested selection paths."""
    explicit={'gap_mask':'gap_path','extent_gap':'extent_gap_path'}.get(field)
    if explicit and item.get(explicit):return item[explicit]
    base=Path(item['path'])
    if item.get('file'):
        relative=Path(item['file'])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid generation artifact reference')
        for part in reversed(relative.parts):
            if base.name!=part:raise ValueError('Generation artifact path contradicts its relative file identity')
            base=base.parent
    else:
        base=base.parent  # Early manifests used names relative to the artifact directory.
    return (base/item[field]).as_posix()


def display_metadata(item, project=None):
    from urllib.parse import quote
    product = item["product"]
    return {"schema_version": "zeus.acquisition/2.0", "dataset_name": selection_label(item['name'],item['parameters']), "category": item["category"], "data_type": item["kind"],
            "source": product["name"], "source_version": product["version"], "provider": product["publisher"], "provider_url": product["endpoint"],
            "documentation_url": product["assessment"]["documentation"][0], "license": product["license"], "attribution": product["attribution"],
            "units": product.get('semantics',{}).get('attribute_units',product['units']) if item['kind']=='table' else product["units"], "native_spacing": product["native_spacing"], "observation_period": product["observation_period"],
            "release_date": product["release_date"], "accuracy": product["accuracy"], "output_grid": item.get("grid"), "target_crs": item.get("crs"),
            "validation_status": item["validation_status"], "sha256": item["sha256"], "scientific_hash": item["scientific_hash"], "role": item.get("role"), "parameters": item["parameters"],
            "bbox_wgs84": item.get("bbox_wgs84"), "dimensions":item.get("dimensions"),
            "download_url": f"/api/projects/{quote(project,safe='')}/datasets/{quote(item['id'],safe='')}/download" if project else None}
