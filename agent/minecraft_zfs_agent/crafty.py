"""Small bounded Crafty v2 client; API success never implies process completion."""
from datetime import datetime
import json
import os
from pathlib import Path
import ssl
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class CraftyError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise CraftyError('Crafty redirects are not allowed')


class CraftyClient:
    def __init__(self, config):
        # Config validation fixes the origin/server; no HTTP request can select either.
        self.base_url = config.base_url.rstrip('/')
        self.server_id = config.server_id
        self.stop_timeout = config.stop_timeout_seconds
        self.start_timeout = config.start_timeout_seconds
        self.poll_interval = 0.5
        fd = os.open(config.token_file,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8192 or info.st_mode & 0o077:
                raise ValueError('Crafty token must be a private regular file')
            self._token = os.read(fd,8193).decode('utf-8').strip()
            if len(self._token) < 32 or any(x.isspace() for x in self._token):
                raise ValueError('invalid Crafty token file')
        finally: os.close(fd)
        context = ssl.create_default_context(cafile=str(config.ca_file) if config.ca_file else None)
        if not config.verify_tls:
            context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
        self.opener = build_opener(_NoRedirect(),HTTPSHandler(context=context))

    def request(self, method, path, body=None):
        raw = None if body is None else json.dumps(body,separators=(',',':')).encode()
        req = Request(self.base_url+path,data=raw,method=method,
                      headers={'Authorization':'Bearer '+self._token,'Content-Type':'application/json'})
        try:
            with self.opener.open(req,timeout=10) as response:
                raw = response.read(1024*1024+1)
                if len(raw)>1024*1024: raise CraftyError('Crafty response too large')
            value = json.loads(raw)
            if not isinstance(value,dict) or value.get('status') != 'ok':
                raise CraftyError('Crafty rejected the request')
            return value.get('data',{})
        except HTTPError as error:
            raise CraftyError('Crafty HTTP request failed with status '+str(error.code)) from None
        except (URLError,TimeoutError,OSError,ValueError):
            # Never retain remote error bodies, URLs, auth headers or arbitrary exception text.
            raise CraftyError('Crafty request failed') from None

    def settings(self):
        data = self.request('GET','/api/v2/servers/'+self.server_id)
        if not isinstance(data,dict) or any(type(data.get(k)) is not bool for k in ('auto_start','crash_detection')):
            raise CraftyError('Crafty returned invalid automatic restart settings')
        return {k:data[k] for k in ('auto_start','crash_detection')}

    def set_policy(self, policy):
        if set(policy) != {'auto_start','crash_detection'} or any(type(v) is not bool for v in policy.values()):
            raise ValueError('invalid automatic restart policy')
        self.request('PATCH','/api/v2/servers/'+self.server_id,policy)
        if self.settings() != policy:
            raise CraftyError('Crafty automatic restart settings did not change')

    def stats(self):
        data = self.request('GET','/api/v2/servers/'+self.server_id+'/stats')
        if (not isinstance(data,dict) or type(data.get('stats_id')) is not int
                or type(data.get('running')) is not bool or not isinstance(data.get('created'),str)):
            raise CraftyError('Crafty returned invalid process statistics')
        try: datetime.fromisoformat(data['created'])
        except ValueError: raise CraftyError('Crafty returned invalid statistics timestamp') from None
        return data

    def action(self, action):
        if action not in ('stop_server','start_server'): raise ValueError('unsupported Crafty action')
        self.request('POST','/api/v2/servers/'+self.server_id+'/action/'+action,{})

    @staticmethod
    def _fresh(value, baseline):
        try:
            return (type(value.get('stats_id')) is int and value['stats_id'] > baseline['stats_id']
                    and datetime.fromisoformat(value['created']) > datetime.fromisoformat(baseline['created']))
        except (KeyError,ValueError,TypeError): return False

    def wait_stopped(self, baseline):
        deadline = time.monotonic()+self.stop_timeout
        while True:
            value = self.stats()
            if self._fresh(value,baseline) and value.get('running') is False: return value
            if time.monotonic() >= deadline: raise CraftyError('Crafty stop confirmation timed out')
            time.sleep(self.poll_interval)

    def wait_started(self, baseline):
        deadline = time.monotonic()+self.start_timeout
        while True:
            value = self.stats()
            # int_ping_results is a CharField in Crafty 4.11.0; online is player count.
            if (self._fresh(value,baseline) and value.get('running') is True
                    and value.get('int_ping_results') in (True,'True','true')): return value
            if time.monotonic() >= deadline: raise CraftyError('Crafty game readiness timed out')
            time.sleep(self.poll_interval)
