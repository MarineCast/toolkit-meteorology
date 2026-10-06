"""Bounded direct HTTPS implementation of the official ECMWF datastore v1 protocol.

Protocol reference: ecmwf-datastores-client 0.5.3 processing.py/profile.py.
No implicit SDK calls, retries, proxies, redirects, downloads, cleanup or logging.
API error bodies, credentials and signed result URLs never appear in exceptions.
"""
from __future__ import annotations

import json
import re
import ssl
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import (Request, build_opener, ProxyHandler, HTTPSHandler,
                            HTTPRedirectHandler)

from .jobs import METADATA_RESERVATION
from .planning import DATASET
from .resources import Budget, LimitExceeded

API = 'https://cds.climate.copernicus.eu/api'


class TransportError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _qualified_url(url, hosts, *, api=False):
    try:
        parsed=urlsplit(url)
        valid=(parsed.scheme=='https' and parsed.hostname in hosts and parsed.port in (None,443)
               and not parsed.username and not parsed.password and not parsed.fragment
               and (not api or (parsed.path.startswith('/api/') and not parsed.query)))
    except (ValueError,TypeError):
        valid=False
    if not valid:
        raise TransportError('Unqualified provider URL; address redacted.')
    return url


class CDSHTTPProvider:
    """One worker, bound to the same persistent Budget used by run_job.

    storage_hosts must be exact reviewed hostnames; no wildcard/suffix acceptance.
    Result URLs are accepted only from this authenticated job's result document.
    Nothing is opened during construction. Credentials remain in memory only.
    """
    handles_http_budget=True

    def __init__(self, *, key: str, budget: Budget, storage_hosts: tuple[str,...],
                 download_cap: int, opener=None, timeout_seconds=15):
        if (not isinstance(key,str) or not key.strip() or '\n' in key or '\r' in key
                or not storage_hosts or any(not re.fullmatch('[a-z0-9.-]+',h) for h in storage_hosts)
                or type(download_cap) is not int or download_cap<=0
                or not 0<timeout_seconds<=30):
            raise ValueError('Valid in-memory credential, exact hosts and positive bounded limits required.')
        self._key=key
        self.budget=budget
        self.storage_hosts=storage_hosts
        self.download_cap=download_cap
        self.timeout=timeout_seconds
        self._opener=opener or build_opener(ProxyHandler({}),HTTPSHandler(context=ssl.create_default_context()),NoRedirect())
        self._terms_verified=False

    def _open(self, url, *, method='GET', data=None, credential=False):
        _qualified_url(url,('cds.climate.copernicus.eu',) if credential else self.storage_hosts,api=credential)
        self.budget.checkpoint()
        remaining=self.budget.limits.seconds-self.budget.receipt()['elapsed_seconds']
        if remaining<=0:raise LimitExceeded('Elapsed HTTP budget exhausted.')
        headers={'Accept-Encoding':'identity','User-Agent':'toolkit-meteorology-bounded-native-pilot/1'}
        if credential:headers['PRIVATE-TOKEN']=self._key
        if data is not None:headers['Content-Type']='application/json'
        request=Request(url,data=data,headers=headers,method=method)
        try:
            response=self._opener.open(request,timeout=min(self.timeout,remaining))
            status=response.status
            if status not in (200,201,202):
                response.close()
                raise TransportError('Unexpected HTTP status; response body redacted.')
            encoding=response.headers.get('Content-Encoding','identity')
            if encoding!='identity':
                response.close();raise TransportError('Encoded response rejected.')
            return response
        except HTTPError as exc:
            code=exc.code;exc.close()
            raise TransportError(f'Provider HTTP {code}; address/body redacted.') from None
        except (TransportError,LimitExceeded):raise
        except Exception:
            raise TransportError('Provider connection failed; details redacted.') from None

    def _json(self,url,*,method='GET',payload=None):
        _qualified_url(url,('cds.climate.copernicus.eu',),api=True)
        data=None if payload is None else json.dumps(payload,allow_nan=False,separators=(',',':')).encode()
        if data is not None and len(data)>METADATA_RESERVATION:
            raise LimitExceeded('Outgoing request metadata exceeds cap.')
        self.budget.reserve_transfer(METADATA_RESERVATION)
        try:
            with self._open(url,method=method,data=data,credential=True) as response:
                length=response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length)>METADATA_RESERVATION):
                    raise LimitExceeded('Provider metadata length exceeds cap.')
                chunks=[];received=0
                while received<METADATA_RESERVATION:
                    self.budget.checkpoint(additional_memory=2*METADATA_RESERVATION)
                    chunk=response.read(min(8192,METADATA_RESERVATION-received))
                    if not chunk:break
                    received+=len(chunk);self.budget.received+=len(chunk)
                    if received>METADATA_RESERVATION:raise LimitExceeded('Provider read exceeded cap.')
                    chunks.append(chunk)
                if (length is None and received==METADATA_RESERVATION) or (length is not None and received!=int(length)):
                    raise TransportError('Metadata is capped or truncated; no extra byte read.')
                value=json.loads(b''.join(chunks))
                if not isinstance(value,(dict,list)):raise ValueError()
                return value
        except (LimitExceeded,TransportError):raise
        except Exception:
            raise TransportError('Invalid or interrupted provider metadata; details redacted.') from None
        finally:self.budget.persist()

    def verify_terms(self, required: list[dict]):
        """Read accepted licences only; never accept/update licences.

        Required IDs/revisions must be reviewed against the current dataset terms.
        Unknown response schema fails closed. Returned receipt omits account data.
        """
        if not required or any(set(item)!={'id','revision'} or not isinstance(item['id'],str)
                               or type(item['revision']) is not int for item in required):
            raise ValueError('Exact reviewed dataset licence IDs/revisions required.')
        catalogue=self._json(API+'/catalogue/v1/collections/'+DATASET)
        declared=[{'id':link.get('id'),'revision':link.get('rev')}
                  for link in catalogue.get('links',[]) if link.get('rel')=='license']
        if catalogue.get('id')!=DATASET or declared!=required:
            raise TransportError('Current dataset licence identity differs from reviewed terms.')
        value=self._json(API+'/profiles/v1/account/licences')
        accepted=value if isinstance(value,list) else value.get('licences')
        if not isinstance(accepted,list):raise TransportError('Unrecognized accepted-licence schema; not verified.')
        if any(not any(isinstance(item,dict) and item.get('id')==need['id'] and item.get('revision')==need['revision']
                       for item in accepted) for need in required):
            raise TransportError('Required dataset terms not verified as accepted; no terms changed.')
        self._terms_verified=True
        return {'status':'VERIFIED_EXISTING_ACCEPTANCE','required':required,'terms_changed':False}

    def submit(self,dataset,request):
        if dataset!=DATASET or not self._terms_verified:
            raise TransportError('ERA5 dataset and verified existing terms required before submit.')
        # Match official get_process().submit(), including a bounded process lookup.
        process=self._json(API+'/retrieve/v1/processes/'+DATASET)
        if process.get('id')!=DATASET:raise TransportError('Provider process identity differs.')
        result=self._json(API+'/retrieve/v1/processes/'+DATASET+'/execution',method='POST',payload={'inputs':request})
        links=[link.get('href') for link in result.get('links',[]) if link.get('rel')=='monitor']
        if len(links)!=1:raise TransportError('Provider did not supply a unique monitor link.')
        url=_qualified_url(links[0],('cds.climate.copernicus.eu',),api=True)
        prefix=API+'/retrieve/v1/jobs/'
        job=url.removeprefix(prefix)
        if not url.startswith(prefix) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',job):
            raise TransportError('Monitor link is not a qualified job identity.')
        return job

    def _job_url(self,job):
        if not isinstance(job,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',job):
            raise TransportError('Invalid job identity.')
        return API+'/retrieve/v1/jobs/'+job

    def status(self,job):
        value=self._json(self._job_url(job))
        if value.get('jobID') not in (None,job) or value.get('processID') not in (None,DATASET):
            raise TransportError('Provider job identity differs.')
        return value.get('status')

    def result_opener(self,job):
        value=self._json(self._job_url(job)+'/results')
        try:
            asset=value['asset']['value']
            url=_qualified_url(asset['href'],self.storage_hosts)
            size=asset['file:size']
            if type(size) is not int or not 0<size<=self.download_cap:
                raise LimitExceeded('Official result size exceeds approved download cap.')
        except (LimitExceeded,TransportError):raise
        except Exception:raise TransportError('Unrecognized official result asset; details redacted.') from None
        def open_response():
            response=self._open(url)  # No PRIVATE-TOKEN on storage GET. Reserved by bounded_transfer.
            if response.headers.get('Content-Length')!=str(size):
                response.close();raise TransportError('Storage Content-Length differs from official result size.')
            return response
        return open_response
