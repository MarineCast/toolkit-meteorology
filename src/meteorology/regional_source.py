"""Bounded, checksummed local archive of ECMWF's geographic ERA5 chunks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler

import numpy as np

from .artifacts import checksum_path
from .core.artifacts import atomic_write_json
from .era5.credentials import resolve_credentials
from .era5.transport import NoRedirect

URL = ('https://arco.datastores.ecmwf.int/cadl-arco-geo-002/arco/'
       'reanalysis_era5_single_levels/sfc/geoChunked.zarr')
UNITS = {'t2m': 'K', 'd2m': 'K', 'u10': 'm s**-1', 'v10': 'm s**-1',
         'msl': 'Pa', 'tcc': '(0 - 1)', 'tp': 'm'}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def fetch(name, limit):
    """Never send the credential to redirects, proxies or another origin."""
    key = resolve_credentials(required=True).key
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
    try:
        with opener.open(Request(URL + '/' + name,
                                 headers={'Authorization': 'Bearer ' + key}), timeout=45) as reply:
            body = reply.read(limit + 1)
    except Exception:
        raise RuntimeError('ECMWF ARCO retrieval failed; credentials and response redacted.') from None
    if len(body) > limit:
        raise ValueError('ECMWF ARCO response exceeded its byte cap.')
    return body


class Archive:
    """Caller holds the cache writer lock. Metadata and completed objects are pinned."""

    def __init__(self, root, bbox, *, workers=4, transfer_bytes=8 * 1024**3,
                 max_seconds=3600, fetcher=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher or fetch
        self.workers = workers
        self.cap = transfer_bytes
        self.max_seconds = max_seconds
        self.started = time.monotonic()
        self.bytes = 0
        self.requests = 0
        self.lock = threading.Lock()
        self.used = {}
        raw = self.object('.zmetadata', limit=2 * 1024**2)
        self.snapshot = hashlib.sha256(raw).hexdigest()
        self.meta = json.loads(raw)['metadata']
        for parameter, units in UNITS.items():
            a = self.meta[parameter + '/.zarray']
            attrs = self.meta[parameter + '/.zattrs']
            if (a['zarr_format'] != 2 or a['chunks'] != [67584, 4, 4]
                    or a['dtype'] != '<f4' or a['order'] != 'C' or a.get('filters')
                    or a.get('dimension_separator', '.') != '.'
                    or a['compressor']['id'] != 'blosc'
                    or attrs.get('units') != units
                    or attrs.get('_ARRAY_DIMENSIONS') != ['time', 'latitude', 'longitude']):
                raise ValueError('Unsupported ECMWF field layout or units: ' + parameter)
        self.lat = self.coordinate('latitude')
        self.lon = self.coordinate('longitude')
        self.times = self.coordinate('time')
        if (self.meta['time/.zattrs']['units'] != 'seconds since 1970-01-01'
                or self.meta['time/.zattrs'].get('calendar') != 'proleptic_gregorian'
                or not np.all(np.diff(self.times) == 3600)
                or not np.allclose(np.diff(self.lat), .25, atol=1e-10, rtol=0)
                or not np.allclose(np.diff(self.lon), .25, atol=1e-10, rtol=0)
                or not np.all(np.isfinite(self.lat)) or not np.all(np.isfinite(self.lon))
                or self.lat[0] != -90 or self.lon[0] != -180):
            raise ValueError('Unsupported ECMWF coordinate axes.')
        for p in UNITS:
            if self.meta[p + '/.zarray']['shape'] != [len(self.times), len(self.lat), len(self.lon)]:
                raise ValueError('ECMWF coordinate/field dimensions differ.')
        w, s, e, n = bbox
        # One grid-cell halo guarantees nearest-source support at bbox edges.
        self.ys = np.flatnonzero((self.lat >= s - .25) & (self.lat <= n + .25))
        self.xs = np.flatnonzero((self.lon >= w - .25) & (self.lon <= e + .25))
        if not len(self.ys) or not len(self.xs) or len(self.ys) * len(self.xs) > 4096:
            raise ValueError('Regional archive supports 1..4096 native halo points.')
        self.coords = np.array([(a, b) for a in self.lat[self.ys] for b in self.lon[self.xs]])
        identity = hashlib.sha256(self.coords.tobytes() + self.snapshot.encode()).hexdigest()
        self.local = self.root / 'arrays' / identity
        self.local.mkdir(parents=True, exist_ok=True)
        self.blocks = {}

    def checkpoint(self):
        if time.monotonic() - self.started > self.max_seconds:
            raise ValueError('Regional acquisition elapsed-time cap exceeded.')

    def object(self, name, *, limit=8 * 1024**2):
        self.checkpoint()
        path = self.root / 'objects' / name
        receipt = path.with_name(path.name + '.receipt.json')
        if path.exists() and receipt.exists():
            record = json.loads(receipt.read_text())
            if (path.stat().st_size > limit or record['sha256'] != checksum_path(path)
                    or record['bytes'] != path.stat().st_size):
                raise ValueError('Corrupt cached ECMWF object: ' + name)
            with self.lock:
                self.used[name] = record
            return path.read_bytes()
        if shutil.disk_usage(self.root).free < limit + 64 * 1024**2:
            raise ValueError('Insufficient disk for regional source object.')
        with self.lock:
            if self.requests >= 20000 or self.bytes + limit > self.cap:
                raise ValueError('Regional request/transfer cap exceeded.')
            self.requests += 1
            self.bytes += limit
        body = self.fetcher(name, limit)
        if len(body) > limit:
            raise ValueError('Regional object exceeded transfer cap.')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.partial')
        with temporary.open('wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        record = dict(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body),
                      retrieved_at_utc=utcnow(), source=URL + '/' + name)
        atomic_write_json(receipt, record, overwrite=True)
        with self.lock:
            self.bytes -= limit - len(body)
            self.used[name] = record
        return body

    def decode(self, name):
        from numcodecs import get_codec
        parameter = name.split('/')[0]
        a = self.meta[parameter + '/.zarray']
        count = int(np.prod(a['chunks']))
        dtype = np.dtype(a['dtype'])
        if (count <= 0 or count * dtype.itemsize > 8 * 1024**2
                or a.get('filters') or a['order'] != 'C'
                or a['compressor']['id'] != 'blosc'):
            raise ValueError('Unsupported or oversized decoded chunk.')
        output = np.empty(count, dtype=dtype)
        get_codec(a['compressor']).decode(self.object(name), out=output)
        return output.reshape(a['chunks'])

    def coordinate(self, name):
        a = self.meta[name + '/.zarray']
        if (len(a['shape']) != 1 or len(a['chunks']) != 1
                or a['shape'][0] > 2_000_000 or a['chunks'][0] <= 0
                or a['dtype'] not in ('<f8', '<i8')):
            raise ValueError('Unsupported coordinate array.')
        return np.concatenate([self.decode(f'{name}/{i}')
                               for i in range((a['shape'][0] + a['chunks'][0] - 1)
                                              // a['chunks'][0])])[:a['shape'][0]]

    def block(self, parameter, index):
        key = (parameter, index)
        if key in self.blocks:
            return self.blocks[key]
        self.checkpoint()
        size = min(67584, len(self.times) - index * 67584)
        path = self.local / f'{parameter}-{index}.npy'
        proof = path.with_suffix('.json')
        if proof.exists() and path.exists():
            record = json.loads(proof.read_text())
            if checksum_path(path) != record['sha256']:
                raise ValueError('Corrupt cached regional array.')
            self.used.update(record['sources'])
        else:
            if shutil.disk_usage(self.root).free < size * len(self.coords) * 4 + 512 * 1024**2:
                raise ValueError('Insufficient disk for regional array and source chunks.')
            tmp = path.with_suffix('.partial.npy')
            output = np.lib.format.open_memmap(tmp, mode='w+', dtype='<f4',
                                              shape=(size, len(self.coords)))
            tasks = [(int(y), int(x)) for y in sorted(set(self.ys // 4))
                     for x in sorted(set(self.xs // 4))]
            names = [f'{parameter}/{index}.{y}.{x}' for y, x in tasks]

            def write(task):
                y, x = task
                values = self.decode(f'{parameter}/{index}.{y}.{x}')[:size]
                ly, lx = np.flatnonzero(self.ys // 4 == y), np.flatnonzero(self.xs // 4 == x)
                ids = (ly[:, None] * len(self.xs) + lx).ravel()
                output[:, ids] = values[:, (self.ys[ly] % 4)[:, None], self.xs[lx] % 4].reshape(size, -1)

            try:
                with ThreadPoolExecutor(self.workers) as executor:
                    list(executor.map(write, tasks))
                output.flush()
                del output
                os.replace(tmp, path)
                atomic_write_json(proof, dict(sha256=checksum_path(path),
                                             sources={name: self.used[name] for name in names}),
                                  overwrite=True)
            finally:
                tmp.unlink(missing_ok=True)
        array = np.load(path, mmap_mode='r', allow_pickle=False)
        if array.shape != (size, len(self.coords)) or array.dtype != np.dtype('<f4'):
            raise ValueError('Regional cache shape/dtype mismatch.')
        # Keep only the current time block's memory maps, not the whole history.
        self.blocks = {k: v for k, v in self.blocks.items() if k[1] == index}
        self.blocks[key] = array
        return array

    def cube(self, day, parameters, *, allow_missing_boundary=False):
        import pandas as pd
        stamps = pd.date_range(day, periods=25, freq='h', tz='UTC').as_unit('ns')
        seconds = stamps.asi8 // 10**9
        positions = np.searchsorted(self.times, seconds)
        supported = positions < len(self.times)
        if not supported.all():
            if (not allow_missing_boundary or not supported[:-1].all()
                    or supported[-1] or seconds[-1] != self.times[-1] + 3600):
                raise ValueError('Requested dates/boundary hour exceed the pinned ERA5 coverage.')
        if not np.array_equal(self.times[positions[supported]], seconds[supported]):
            raise ValueError('Requested source time coordinates differ.')
        values = []
        evidence = {}
        for p in parameters:
            rows = [self.block(p, int(h // 67584))[h % 67584] if good
                    else np.full(len(self.coords), np.nan)
                    for h, good in zip(positions, supported)]
            values.append(np.array(rows, dtype=np.float64))
            for h, stamp in enumerate(stamps):
                evidence[f'{len(values) - 1}/{h}'] = dict(
                    parameter=p, valid_time_utc=stamp.isoformat(),
                    source_metadata_sha256=self.snapshot, backend='ECMWF_ARCO',
                    revision_init_lead='unavailable')
        return dict(values=np.array(values), coordinates=self.coords, times=stamps.asi8,
                    parameters=np.array(parameters), metadata=dict(
                        grid_hash=hashlib.sha256(self.coords.tobytes()).hexdigest(), evidence=evidence))
