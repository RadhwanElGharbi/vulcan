from __future__ import annotations

from pathlib import Path
from typing import Optional

from .constants import GDALWARP_BIN, OGR2OGR_BIN
from .job_state import DatasetJobState, _run_command
from .models import DatasetDefinition, FetchContext


def _process_raster(
    defn: DatasetDefinition,
    ctx: FetchContext,
    raw_path: Path,
    job: DatasetJobState,
    output_path: Optional[Path] = None,
) -> None:
    processed_path = output_path or defn.processed_path(ctx)
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = processed_path.with_name(f"{processed_path.stem}.tmp{processed_path.suffix}")
    if tmp_path.exists():
        tmp_path.unlink()
    cmd = [
        GDALWARP_BIN,
        "-t_srs",
        f"EPSG:{ctx.target_epsg}",
        "-cutline",
        str(ctx.cutline_path),
        "-crop_to_cutline",
        "-of",
        "GTiff",
        "-co",
        "COMPRESS=LZW",
        "-co",
        "TILED=YES",
        "-co",
        "BIGTIFF=IF_SAFER",
        "-wo",
        "NUM_THREADS=ALL_CPUS",
        "-multi",
        "-r",
        defn.resampling,
        str(raw_path),
        str(tmp_path),
    ]
    if defn.nodata is not None:
        cmd.extend(["-dstnodata", str(defn.nodata)])
    if defn.key in {"soil", "geohazard"}:
        # Preserve cells intersecting AOIs smaller than a native source pixel.
        cmd.extend(["-wo", "CUTLINE_ALL_TOUCHED=TRUE"])
    _run_command(cmd, ctx.project_path, job, ctx, f"{defn.label} reprojection")
    tmp_path.replace(processed_path)


def _process_vector(
    defn: DatasetDefinition,
    ctx: FetchContext,
    raw_path: Path,
    job: DatasetJobState,
    output_path: Optional[Path] = None,
) -> None:
    """
    Process vector data: reproject and clip to AOI.
    Preserves ALL original attributes from any source.
    """
    processed_path = output_path or defn.processed_path(ctx)
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = processed_path.with_suffix(".tmp.gpkg")
    if tmp_path.exists():
        tmp_path.unlink()
    
    cmd = [
        OGR2OGR_BIN,
        "-f",
        "GPKG",
        "-t_srs",
        f"EPSG:{ctx.target_epsg}",
        "-clipsrc",
        str(ctx.cutline_path),
        "-makevalid",
        "-nlt",
        "PROMOTE_TO_MULTI",
        "-lco",
        "SPATIAL_INDEX=YES",
        "-overwrite",
        str(tmp_path),
        str(raw_path),
    ]
    _run_command(cmd, ctx.project_path, job, ctx, f"{defn.label} processing")
    
    tmp_path.replace(processed_path)
