"""Preserve project AOI inputs and explicitly record normalization operations."""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

from .contracts import Finding, digest, file_hash
from .projection import BASIS, resolve_operation

MAX_AOI_BYTES = 64 * 1024**2


def read_aoi(path, *, frozen=None):
    from osgeo import gdal
    from pyproj import CRS, Transformer
    from shapely import force_2d, normalize
    from shapely.geometry import GeometryCollection, mapping, shape
    from shapely.ops import transform, unary_union
    from .spatial import normalize_aoi

    path = Path(path).resolve()
    source = gdal.OpenEx(str(path), gdal.OF_VECTOR | gdal.OF_READONLY)
    if source is None:
        raise ValueError('AOI cannot be opened')
    dependencies = sorted({Path(p).resolve() for p in source.GetFileList() or [path]})
    source = None
    if path not in dependencies:
        raise ValueError('AOI driver did not identify its main input')
    if any(p.parent != path.parent or not p.is_file() for p in dependencies):
        raise ValueError('AOI contains external or unsupported dependencies')
    if sum(p.stat().st_size for p in dependencies) > MAX_AOI_BYTES:
        raise ValueError('AOI inputs exceed the 64 MiB limit')
    records = []
    with tempfile.TemporaryDirectory(prefix='zeus-aoi-') as directory:
        for dependency in dependencies:
            payload = dependency.read_bytes()
            if sum(v['size'] for v in records) + len(payload) > MAX_AOI_BYTES:
                raise ValueError('AOI inputs exceed the 64 MiB limit')
            copy = Path(directory) / dependency.name
            copy.write_bytes(payload)
            records.append({'filename': dependency.name, 'sha256': file_hash(copy), 'size': len(payload)})
        dataset = gdal.OpenEx(str(Path(directory) / path.name), gdal.OF_VECTOR | gdal.OF_READONLY)
        if dataset is None or dataset.GetLayerCount() != 1:
            raise ValueError('AOI must identify exactly one vector layer')
        layer = dataset.GetLayer(0)
        source_crs = layer.GetSpatialRef()
        if source_crs is None:
            raise ValueError('AOI CRS is missing')
        source_wkt = source_crs.ExportToWkt()
        geographic_source = CRS.from_wkt(source_wkt).is_geographic
        geometries = []
        for feature in layer:
            geometry = feature.GetGeometryRef()
            if geometry is None or geometry.HasCurveGeometry():
                raise ValueError('AOI has missing or unsupported curved geometry')
            value = force_2d(shape(json.loads(geometry.ExportToJson())))
            if value.is_empty or (not geographic_source and not value.is_valid) or value.geom_type not in ('Polygon', 'MultiPolygon'):
                raise ValueError('AOI must contain valid nonempty polygons; no automatic repair')
            geometries.append(value)
            if len(geometries) > 100_000:
                raise ValueError('AOI exceeds the feature limit')
        feature = None; layer = None; dataset = None
        if not geometries:
            raise ValueError('AOI is empty')
        original = GeometryCollection(geometries)
        # A recorded preliminary operation establishes the geographic search
        # extent. Only the final region-specific operation defines the AOI.
        if frozen is not None:
            if frozen['inputs'] != records or frozen['source_crs'] != source_wkt or frozen['entrypoint'] != path.name:
                raise ValueError('Preserved AOI source inputs differ from the frozen plan')
            preliminary = frozen['preliminary_extent_operation']
            operation = frozen['coordinate_operation']
        else:
            preliminary = resolve_operation(source_wkt, 4326, None, always_xy=True)
            projected = transform(Transformer.from_pipeline(preliminary['pipeline']).transform, original)
            if not all(math.isfinite(v) for v in projected.bounds):
                raise ValueError('AOI coordinate transformation failed')
            operation = resolve_operation(source_wkt, 4326, mapping(projected), always_xy=True)
        projected = transform(Transformer.from_pipeline(operation['pipeline']).transform, original)
        final = normalize(unary_union([shape(normalize_aoi(mapping(g))) for g in projected.geoms]))
        if final.is_empty or not final.is_valid or not all(math.isfinite(v) for v in final.bounds) or final.bounds[1] < -90 or final.bounds[3] > 90:
            raise ValueError('AOI normalization produced invalid geographic coordinates')
        if frozen is None:
            checked = resolve_operation(source_wkt, 4326, mapping(final), always_xy=True)
            if checked['pipeline'] != operation['pipeline']:
                raise ValueError('AOI operation does not remain stable for its normalized extent')
    for record in records:
        if file_hash(path.parent / record['filename']) != record['sha256']:
            raise ValueError('AOI input changed while normalizing it')
    evidence = {'inputs': records, 'entrypoint': path.name, 'source_crs': source_wkt,
                'preliminary_extent_operation': preliminary, 'coordinate_operation': operation,
                'geometry_policy': 'Use the horizontal polygon footprint; preserve holes, union features, split at the antimeridian, canonicalize ring order. Source vertices are transformed without geodesic densification. Original dimensions and attributes remain in retained inputs.',
                'normalized_aoi_hash': digest(mapping(final))}
    if frozen is not None and evidence != frozen:
        raise ValueError('Preserved AOI normalization differs from the frozen plan')
    return mapping(final), evidence


def replay_aoi(selection, plan, store):
    frozen = selection.recipe.get('aoi_normalization')
    if frozen is None:
        return
    receipts = [d for d in selection.discovery if d.get('role') == 'original_project_aoi']
    expected = {r['filename']: r for r in frozen['inputs']}
    if len(receipts) != len(expected) or {r['filename'] for r in receipts} != set(expected):
        raise ValueError('Preserved AOI dependencies are incomplete')
    with tempfile.TemporaryDirectory(prefix='zeus-replay-aoi-') as directory:
        for entry in receipts:
            name = entry['filename']
            if Path(name).name != name or ':' in name:
                raise ValueError('Invalid preserved AOI filename')
            record = expected[name]
            receipt = entry['receipt']
            if receipt['sha256'] != record['sha256'] or receipt['size'] != record['size']:
                raise ValueError('Preserved AOI receipt contradicts the confirmed input')
            raw = store.verify_blob(record['sha256']).read_bytes()
            (Path(directory) / name).write_bytes(raw)
        geometry, _ = read_aoi(Path(directory) / frozen['entrypoint'], frozen=frozen)
        if digest(geometry) != plan.aoi_hash:
            raise ValueError('Replayed AOI differs from the confirmed geographic footprint')


def preserve_aoi(ctx, transport):
    evidence = getattr(ctx, 'aoi_provenance', None)
    if evidence is None:
        return [], []  # Programmatic immutable fixture AOIs have no source file.
    receipts = []
    for record in evidence['inputs']:
        raw = (Path(ctx.cutline_path).parent / record['filename']).read_bytes()
        receipt = transport.retain_bytes(raw, 'zeus:aoi/' + record['filename'])
        if receipt.sha256 != record['sha256'] or receipt.size != record['size']:
            raise ValueError('AOI input changed before the plan could be frozen')
        receipts.append({'receipt': receipt.model_dump(mode='json'), 'role': 'original_project_aoi', 'filename': record['filename']})
    operation = evidence['coordinate_operation']
    findings = []
    if not operation['best_known_operation_available'] or operation['stated_accuracy_m'] is None:
        findings.append(Finding(id='aoi-normalization-accuracy', rule='aoi-coordinate-operation', severity='acknowledgement', basis=BASIS,
            message=f"The project AOI was normalized using {operation['name']}; stated operation accuracy is {operation['stated_accuracy_m'] if operation['stated_accuracy_m'] is not None else 'unknown'} m. Missing higher-accuracy grids: {', '.join(operation['missing_grids']) or 'none'}.", evidence=evidence))
    return receipts, findings
