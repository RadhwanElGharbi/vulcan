"""Explicit longitude normalization; geometry repair is never implicit."""
from __future__ import annotations

import math


def normalize_aoi(value):
    from shapely.geometry import shape, Polygon, MultiPolygon, LineString, mapping
    from shapely.ops import split, unary_union
    from shapely.affinity import translate
    from shapely import normalize
    geometry = shape(value)
    if geometry.geom_type not in ("Polygon", "MultiPolygon") or geometry.is_empty:
        raise ValueError("AOI must be a nonempty Polygon or MultiPolygon")
    polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    result = []
    for polygon in polygons:
        rings = []
        for ring in [polygon.exterior, *polygon.interiors]:
            points = []
            for x,y,*rest in ring.coords:
                if not math.isfinite(x) or not math.isfinite(y) or not -180 <= x <= 180 or not -90 <= y <= 90:
                    raise ValueError("AOI contains invalid longitude or latitude")
                if points:
                    while x-points[-1][0] > 180: x -= 360
                    while x-points[-1][0] < -180: x += 360
                    if abs(x-points[-1][0]) == 180:
                        raise ValueError("An AOI edge spans exactly 180 degrees and is ambiguous")
                points.append((x,y))
            if points[0] != points[-1]:
                raise ValueError("A polygon winding around a pole needs an explicit polar AOI representation")
            if rings:
                offset = round((sum(x for x,y in rings[0])/len(rings[0])-sum(x for x,y in points)/len(points))/360)*360
                points = [(x+offset,y) for x,y in points]
            rings.append(points)
        unwrapped = Polygon(rings[0], rings[1:])
        if not unwrapped.is_valid:
            raise ValueError("AOI geometry is invalid; repair must be explicit")
        parts = [unwrapped]
        left,right=unwrapped.bounds[0],unwrapped.bounds[2]
        for meridian in range(math.floor((left+180)/360)*360+180, math.ceil((right+180)/360)*360, 360):
            if left < meridian < right:
                line = LineString([(meridian,-91),(meridian,91)])
                parts = [piece for part in parts for piece in split(part,line).geoms]
        for part in parts:
            shift = math.floor((part.representative_point().x+180)/360)*360
            result.append(translate(part,xoff=-shift))
    combined = normalize(unary_union(result))
    if combined.is_empty or not combined.is_valid:
        raise ValueError("AOI longitude normalization failed")
    return mapping(combined)
