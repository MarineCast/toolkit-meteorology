"""Explicit shared marine membership consumption and source-native H3 sampling.

The shared owner's registry-artifact-interface.v1 defines exact ASCII/LF membership
bytes. This consumer captures one byte snapshot and does not create a shared format,
create water membership, or certify coastline geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
from pathlib import Path

import h3
import numpy as np
import pandas as pd
from pyproj import Geod
from shapely.geometry import Point, Polygon, box, mapping

from .artifacts import checksum_path
from .study import canonical_bytes, load_study_config, validate_study_identity, resolve_config_path

GEOD=Geod(ellps='WGS84')
SAMPLING_METHOD='marine-reporting-full-cell-centroid-nearest-native-atmosphere-v1'


@dataclass(frozen=True)
class ReportingMembership:
    """Exact loaded IDs and captured evidence; geometry qualification is external."""
    cells: tuple[str,...]
    resolution: int
    study_identity: dict
    artifact_sha256: str
    canonical_membership_sha256: str
    mask_sha256: str
    mask_revision: str


def _parse_membership_ids(raw: bytes) -> tuple[str,...]:
    """Consume the coordinated owner interface, not permissive CSV/JSON parsing."""
    if raw==b'': return ()
    if not raw.endswith(b'\n') or b'\r' in raw:
        raise ValueError('Membership bytes require LF and terminal newline.')
    try: lines=raw.decode('ascii').split('\n')[:-1]
    except UnicodeDecodeError:
        raise ValueError('Membership bytes require ASCII canonical IDs.') from None
    if any(not line or line.strip()!=line for line in lines):
        raise ValueError('Membership bytes must have no blank lines, headers or whitespace.')
    return tuple(lines)


def load_reporting_membership(study_config, *, mask_path: str | Path,
                              mask_hash_policy: str,
                              resolution: int=5) -> ReportingMembership:
    """Consume exact owner-declared R5 support after production gates.

    Membership path is config-relative and constrained beneath resolved Data.
    mask_path is an additional explicitly supplied witness supported only when
    the owner mask manifest declares exact-file SHA256. Other geometry hash
    policies require their matching verifier; this function never guesses one.
    """
    selected=resolve_config_path(study_config)
    identity=load_study_config(selected)
    if identity is None: raise ValueError('Marine reporting requires explicit shared study selection.')
    if resolution!=identity['study_config']['products']['meteorology']['h3_resolution']:
        raise ValueError('Meteorology marine reporting uses the configured R5 registry only.')
    registry=identity['study_config']['grid_registry']
    entries=[m for m in registry['memberships'] if m['resolution']==resolution and m['role']=='water_reporting']
    if len(entries)!=1: raise ValueError('Registry must declare exactly one water_reporting membership at R5.')
    entry=entries[0]
    root=Path(identity['resolved_data_root'])
    relative=Path(entry['relative_path'])
    if relative.is_absolute():
        raise ValueError('Membership artifact path must be relative to selected config.')
    path=(selected.parent/relative).resolve()
    if not path.is_relative_to(root): raise ValueError('Config-relative membership artifact must remain beneath shared Data root.')
    raw=path.read_bytes()  # Parse and byte hash are bound to one immutable snapshot.
    artifact_hash=hashlib.sha256(raw).hexdigest()
    if artifact_hash!=entry['sha256']: raise ValueError('Membership artifact SHA256 mismatch.')
    if mask_hash_policy!='sha256_exact_file_bytes':
        raise ValueError('Mask hash policy requires its matching owner verifier; no guessed file-byte identity.')
    if not Path(mask_path).is_file():
        raise ValueError('Exact-file mask witness must be a regular file.')
    mask_hash=checksum_path(Path(mask_path))
    if mask_hash!=registry['mask_sha256']: raise ValueError('Materialized mask SHA256 mismatch.')
    cells=_parse_membership_ids(raw)
    if len(cells)!=entry['count']:
        raise ValueError('Membership count differs from declared water-reporting registry.')
    if any(not isinstance(c,str) or c!=c.lower() or not h3.is_valid_cell(c) or
           h3.get_resolution(c)!=resolution for c in cells):
        raise ValueError('Membership requires valid lowercase H3 IDs at the declared resolution.')
    if cells!=tuple(sorted(set(cells))):
        raise ValueError('Membership artifact IDs must already be sorted and unique.')
    canonical_hash=hashlib.sha256(('\n'.join(cells)+'\n').encode() if cells else b'').hexdigest()
    return ReportingMembership(cells,resolution,identity,artifact_hash,canonical_hash,
                               mask_hash,registry['mask_revision'])



def _require_qualified_membership(membership):
    identity=membership.study_identity
    validate_study_identity(identity)
    config=identity['study_config']
    domain,registry=config['domain'],config['grid_registry']
    policy=domain['selection_policy']
    if (domain['status']!='approved' or domain['geometry_status']!='source_relative_validated' or
        policy['status']!='approved' or policy['mask_status']!='source_relative_validated' or
        registry['status']!='validated'):
        raise ValueError('Marine mapping requires approved and qualified geometry, mask and registry.')
    declarations=[entry for entry in registry['memberships'] if entry['role']=='water_reporting'
                  and entry['resolution']==membership.resolution]
    if (len(declarations)!=1 or declarations[0]['sha256']!=membership.artifact_sha256 or
        membership.canonical_membership_sha256!=membership.artifact_sha256 or
        declarations[0]['count']!=len(membership.cells) or membership.resolution!=5 or registry['mask_sha256']!=membership.mask_sha256 or
        registry['mask_revision']!=membership.mask_revision or
        membership.cells!=tuple(sorted(set(membership.cells))) or
        any(not h3.is_valid_cell(c) or c!=c.lower() or h3.get_resolution(c)!=5 for c in membership.cells) or
        hashlib.sha256(('\n'.join(membership.cells)+'\n').encode() if membership.cells else b'').hexdigest()!=membership.canonical_membership_sha256):
        raise ValueError('Reporting membership differs from its pinned registry declaration.')


def _native_points(frame: pd.DataFrame, source_family: str) -> pd.DataFrame:
    if source_family not in {'ERA5','HRRR'} or set(frame.SOURCE_MODEL)!={source_family}:
        raise ValueError('Native source family must be explicit and separate ERA5 or HRRR.')
    if frame.empty or frame.SOURCE_GRID_HASH.nunique()!=1:
        raise ValueError('Native chunk must represent one nonempty source grid.')
    columns=['SOURCE_GRID_INDEX','SOURCE_LAT','SOURCE_LON','SOURCE_GRID_HASH']
    points=frame[columns].drop_duplicates().sort_values('SOURCE_GRID_INDEX')
    if points.SOURCE_GRID_INDEX.duplicated().any() or any(
            type(i) not in (int,np.int32,np.int64) or i<0 for i in points.SOURCE_GRID_INDEX):
        raise ValueError('Native grid indices must identify one unique nonnegative point.')
    coords=points[['SOURCE_LAT','SOURCE_LON']].to_numpy(dtype=float)
    if not np.isfinite(coords).all() or (abs(coords[:,0])>90).any() or (abs(coords[:,1])>180).any():
        raise ValueError('Invalid native coordinates.')
    if points[['SOURCE_LAT','SOURCE_LON']].duplicated().any():
        raise ValueError('Native grid has duplicate coordinates.')
    return points


def nearest_native_crosswalk(native: pd.DataFrame, membership: ReportingMembership, *,
                             source_family: str,
                             hrrr_qualified_footprint: Polygon | None=None,
                             hrrr_max_distance_m: float | None=None) -> tuple[pd.DataFrame,dict]:
    """Sample full H3 centroids on qualified native support, without downscaling.

    ERA5 uses its complete regular distribution grid and no outside-edge extension.
    HRRR requires a decoder-qualified footprint supplied explicitly by its producer.
    The reporting cell set is independent of centroid containment in water.
    """
    _require_qualified_membership(membership)
    points=_native_points(native,source_family)
    if source_family=='ERA5':
        lat=np.sort(points.SOURCE_LAT.unique());lon=np.sort(points.SOURCE_LON.unique())
        if len(lat)<2 or len(lon)<2 or len(lat)*len(lon)!=len(points) or any(
                not np.allclose(np.diff(axis),.25,rtol=0,atol=1e-8) for axis in (lat,lon)):
            raise ValueError('ERA5 mapping requires a complete two-dimensional 0.25 degree distribution grid.')
        footprint=box(lon[0],lat[0],lon[-1],lat[-1])
        footprint_method='CDS_grid_centres_closed_rectangle_no_edge_extrapolation'
    else:
        footprint=hrrr_qualified_footprint
        if hrrr_max_distance_m is None or not np.isfinite(hrrr_max_distance_m) or hrrr_max_distance_m<=0:
            raise ValueError('HRRR mapping requires a positive producer-qualified spacing/distance allowance.')
        if not isinstance(footprint,Polygon) or not footprint.is_valid or footprint.is_empty:
            raise ValueError('HRRR mapping requires an explicit producer-qualified native footprint.')
        if any(not footprint.covers(Point(lon,lat)) for lat,lon in
               points[['SOURCE_LAT','SOURCE_LON']].itertuples(index=False,name=None)):
            raise ValueError('HRRR native points lie outside the declared qualified footprint.')
        footprint_method='caller_producer_qualified_HRRR_native_footprint'
    footprint_hash=hashlib.sha256(canonical_bytes(mapping(footprint))).hexdigest()
    native_coords_hash=hashlib.sha256(points.to_json(orient='records',double_precision=15).encode()).hexdigest()
    rows=[]
    for cell in membership.cells:
        lat,lon=h3.cell_to_latlng(cell)
        supported=footprint.covers(Point(lon,lat))
        record=dict(H3_INDEX=cell,H3_RESOLUTION=membership.resolution,CENTROID_LAT=lat,CENTROID_LON=lon,
                    SOURCE_MODEL=source_family,SOURCE_GRID_HASH=points.SOURCE_GRID_HASH.iloc[0],
                    SPATIAL_SUPPORT_STATUS='SUPPORTED' if supported else 'UNAVAILABLE_OUTSIDE_NATIVE_FOOTPRINT',
                    SOURCE_GRID_INDEX=None,SOURCE_LAT=None,SOURCE_LON=None,NATIVE_DISTANCE_M=None)
        if supported:
            _,_,distances=GEOD.inv(np.full(len(points),lon),np.full(len(points),lat),
                                  points.SOURCE_LON.to_numpy(),points.SOURCE_LAT.to_numpy())
            chosen=points.iloc[int(np.argmin(distances))]
            if source_family=='HRRR' and float(np.min(distances))>hrrr_max_distance_m:
                record['SPATIAL_SUPPORT_STATUS']='UNAVAILABLE_BEYOND_NATIVE_DISTANCE_SUPPORT'
                rows.append(record)
                continue
            record.update(SOURCE_GRID_INDEX=int(chosen.SOURCE_GRID_INDEX),SOURCE_LAT=float(chosen.SOURCE_LAT),
                          SOURCE_LON=float(chosen.SOURCE_LON),NATIVE_DISTANCE_M=float(np.min(distances)))
        rows.append(record)
    frame=pd.DataFrame(rows,columns=['H3_INDEX','H3_RESOLUTION','CENTROID_LAT','CENTROID_LON','SOURCE_MODEL','SOURCE_GRID_HASH','SPATIAL_SUPPORT_STATUS','SOURCE_GRID_INDEX','SOURCE_LAT','SOURCE_LON','NATIVE_DISTANCE_M'])
    frame['SOURCE_GRID_INDEX']=pd.array(frame.SOURCE_GRID_INDEX,dtype='Int64')
    metadata=dict(method=SAMPLING_METHOD,source_family=source_family,
                  native_grid_hash=points.SOURCE_GRID_HASH.iloc[0],native_coordinates_sha256=native_coords_hash,
                  native_footprint_sha256=footprint_hash,native_footprint_method=footprint_method,
                  hrrr_max_distance_m=hrrr_max_distance_m if source_family=='HRRR' else None,
                  membership_artifact_sha256=membership.artifact_sha256,
                  canonical_membership_sha256=membership.canonical_membership_sha256,
                  mask_sha256=membership.mask_sha256,mask_revision=membership.mask_revision,
                  registry_interface='registry-artifact-interface.v1',
                  membership_geometry_retention='full H3 polygons and clipped water support retained in owner mask/registry artifacts; consumer does not rewrite them',
                  config_sha256=membership.study_identity['config_sha256'],h3_version=h3.__version__,
                  reporting_cells=len(frame),supported_cells=int((frame.SPATIAL_SUPPORT_STATUS=='SUPPORTED').sum()),
                  representation='nearest native atmospheric sample at full H3 centroid; not clipped-water mean or fine-scale weather',
                  native_retention='native input remains separate; repeated H3 samples add no ERA5 spatial information',
                  coordinate_engine='H3 cell centroid and PyProj WGS84 ellipsoidal distance',
                  positive_area_engine='not recomputed; consume owner-qualified cell polygons/clipped support')
    metadata['crosswalk_sha256']=hashlib.sha256(frame.to_json(orient='records',double_precision=15).encode()).hexdigest()
    return frame,metadata


def project_native_daily(native: pd.DataFrame, crosswalk: pd.DataFrame, metadata: dict,
                         membership: ReportingMembership, *,source_family: str) -> tuple[pd.DataFrame,dict]:
    """Left-project actual native days onto exact water reporting IDs; no new dates."""
    _require_qualified_membership(membership)
    points=_native_points(native,source_family)
    identity=membership.study_identity
    validate_study_identity(identity)
    if (metadata.get('method')!=SAMPLING_METHOD or metadata.get('source_family')!=source_family or
        metadata.get('native_grid_hash')!=points.SOURCE_GRID_HASH.iloc[0] or
        metadata.get('native_coordinates_sha256')!=hashlib.sha256(points.to_json(orient='records',double_precision=15).encode()).hexdigest() or
        metadata.get('membership_artifact_sha256')!=membership.artifact_sha256 or
        metadata.get('canonical_membership_sha256')!=membership.canonical_membership_sha256 or
        metadata.get('mask_sha256')!=membership.mask_sha256 or metadata.get('config_sha256')!=identity['config_sha256'] or
        metadata.get('crosswalk_sha256')!=hashlib.sha256(crosswalk.to_json(orient='records',double_precision=15).encode()).hexdigest()):
        raise ValueError('Crosswalk differs from its exact source, membership or checksum identity.')
    if tuple(crosswalk.H3_INDEX)!=membership.cells or (len(crosswalk)>0 and set(crosswalk.H3_RESOLUTION)!={membership.resolution}):
        raise ValueError('Crosswalk must preserve exact reporting membership.')
    supported=crosswalk.SPATIAL_SUPPORT_STATUS=='SUPPORTED'
    if (not set(crosswalk.SPATIAL_SUPPORT_STATUS)<= {'SUPPORTED','UNAVAILABLE_OUTSIDE_NATIVE_FOOTPRINT','UNAVAILABLE_BEYOND_NATIVE_DISTANCE_SUPPORT'} or
        crosswalk.loc[supported,'SOURCE_GRID_INDEX'].isna().any() or
        not set(crosswalk.loc[supported,'SOURCE_GRID_INDEX'])<=set(points.SOURCE_GRID_INDEX) or
        crosswalk.loc[~supported,'SOURCE_GRID_INDEX'].notna().any()):
        raise ValueError('Crosswalk native point/status support is inconsistent.')
    if native.duplicated(['DATE','SOURCE_GRID_INDEX']).any() or set(native.TIMEZONE)!={'UTC'}:
        raise ValueError('Native daily rows require unique point/day keys and UTC support.')
    request=identity['requested_time']
    days=sorted(set(native.DATE))
    if any(not date.fromisoformat(request['start'])<=date.fromisoformat(d)<date.fromisoformat(request['end_exclusive']) for d in days):
        raise ValueError('Actual native day lies outside requested study interval.')
    if any(set(group.SOURCE_GRID_INDEX)!=set(points.SOURCE_GRID_INDEX) for _,group in native.groupby('DATE')):
        raise ValueError('Each actual native day must retain the full source point universe, including nulls.')
    other='HRRR' if source_family=='ERA5' else 'ERA5'
    if any(c.startswith(other+'_') for c in native.columns):
        raise ValueError('Foreign source metric columns cannot be blended into a marine source product.')
    metrics=[c for c in native.columns if c.startswith(source_family+'_') and c.endswith('_STATUS')]
    if not metrics: raise ValueError('Native daily metrics require source-prefixed status/coverage contracts.')
    contracted={status.removesuffix('_STATUS')+suffix for status in metrics
                for suffix in ('','_STATUS','_VALID_HOURS','_EXPECTED_HOURS','_COVERAGE_FRACTION')}
    if any(c.startswith(source_family+'_') and c not in contracted for c in native.columns):
        raise ValueError('Uncontracted source metric column; every metric requires complete status/count/coverage.')
    for status in metrics:
        value=status.removesuffix('_STATUS')
        cols=[value,value+'_VALID_HOURS',value+'_EXPECTED_HOURS',value+'_COVERAGE_FRACTION']
        if any(c not in native for c in cols):
            raise ValueError('Native metric value/count/coverage contract is incomplete.')
        count=native[value+'_VALID_HOURS']
        if (count.isna().any() or ((count<0)|(count>24)|(count!=np.floor(count))).any() or
                (native[value+'_EXPECTED_HOURS']!=24).any() or
                not np.allclose(native[value+'_COVERAGE_FRACTION'],count/24,rtol=0,atol=1e-12)):
            raise ValueError('Native daily count/coverage contract disagrees.')
        expected=np.where(count==24,'COMPLETE',np.where(count>0,'PARTIAL','UNAVAILABLE'))
        if (native[status]!=expected).any() or native.loc[count!=24,value].notna().any() or not np.isfinite(native.loc[count==24,value].astype(float)).all():
            raise ValueError('Native metric status/value contract disagrees with complete UTC support.')
    outputs=[]
    source=native.drop(columns=['SOURCE_MODEL','SOURCE_GRID_HASH','SOURCE_LAT','SOURCE_LON'])
    for day in days:
        subset=source[source.DATE==day]
        result=crosswalk.merge(subset,on='SOURCE_GRID_INDEX',how='left',validate='many_to_one')
        unavailable=result.SPATIAL_SUPPORT_STATUS!='SUPPORTED'
        result['DATE']=day
        result['TIMEZONE']='UTC'
        for status in metrics:
            value=status.removesuffix('_STATUS')
            for suffix in ('','_VALID_HOURS','_EXPECTED_HOURS','_COVERAGE_FRACTION'):
                if value+suffix not in native: raise ValueError('Native metric value/count/coverage contract is incomplete.')
            result.loc[unavailable,value]=np.nan
            result.loc[unavailable,status]='UNAVAILABLE'
            result.loc[unavailable,value+'_VALID_HOURS']=0
            result.loc[unavailable,value+'_EXPECTED_HOURS']=24
            result.loc[unavailable,value+'_COVERAGE_FRACTION']=0
        outputs.append(result)
    output=pd.concat(outputs,ignore_index=True).sort_values(['DATE','H3_INDEX']).reset_index(drop=True)
    return output,dict(source_family=source_family,method=SAMPLING_METHOD,shared_study=identity,
                       reporting_crosswalk=metadata,requested_time=request,
                       actual_daily_coverage=dict(start=days[0],end_exclusive=(date.fromisoformat(days[-1])+timedelta(days=1)).isoformat(),
                                                  available_days=days,period_complete=False),
                       no_temporal_imputation=True,no_source_blending=True,
                       native_model_representation='retain_native_source_separately',
                       source_as_of='retain native retrieval/consolidation columns; historical publication time unknown')
