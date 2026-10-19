"""Small bounded Crafty v2 client; API success never implies process completion."""
from contextvars import ContextVar
from datetime import datetime
from http.client import HTTPConnection, HTTPSConnection, HTTPException
import json
import os
from pathlib import Path
import socket
import ssl
import threading
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


class CraftyError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise CraftyError('Crafty redirects are not allowed')


_ACTIVE_BUDGET = ContextVar('crafty_request_budget', default=None)


class _RequestBudget:
    """Interrupt an in-progress header/body read even when bytes keep arriving."""
    def __init__(self, deadline):
        self.deadline=deadline
        self.sockets=[]
        self.lock=threading.Lock()
        self.timer=threading.Timer(self.remaining(),self.abort)
        self.timer.daemon=True
        self.timer.start()

    def remaining(self):
        remaining=self.deadline-time.monotonic()
        if remaining <= 0: raise TimeoutError('Crafty request elapsed deadline')
        return remaining

    @staticmethod
    def _shutdown(sock):
        # close alone does not wake a concurrent recv using socket.makefile().
        try:sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass

    def attach(self,sock):
        with self.lock:
            self.sockets.append(sock)
            expired=time.monotonic() >= self.deadline
        if expired:self._shutdown(sock)
        self.remaining()

    def abort(self):
        with self.lock:sockets=list(self.sockets)
        for sock in sockets:self._shutdown(sock)

    def close(self):
        self.timer.cancel()


class _BoundHTTPConnection(HTTPConnection):
    def connect(self):
        super().connect()
        budget=_ACTIVE_BUDGET.get()
        if budget is not None:budget.attach(self.sock)


class _BoundHTTPSConnection(HTTPSConnection):
    def connect(self):
        # The SSL handshake uses the remaining socket timeout; register the TLS
        # socket before urllib begins reading HTTP headers or body bytes.
        super().connect()
        budget=_ACTIVE_BUDGET.get()
        if budget is not None:budget.attach(self.sock)


class _BoundHTTPHandler(HTTPHandler):
    def http_open(self,request):
        return self.do_open(_BoundHTTPConnection,request)


class _BoundHTTPSHandler(HTTPSHandler):
    def https_open(self,request):
        return self.do_open(_BoundHTTPSConnection,request,context=self._context,
                            check_hostname=self._check_hostname)


class CraftyClient:
    def __init__(self, config):
        # Config validation fixes the origin/server; no HTTP request can select either.
        self.base_url = config.base_url.rstrip('/')
        self.server_id = config.server_id
        self.stop_timeout = config.stop_timeout_seconds
        self.start_timeout = config.start_timeout_seconds
        self.poll_interval = 0.5
        self.request_timeout = 10
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
        self.opener = build_opener(ProxyHandler({}),_NoRedirect(),_BoundHTTPHandler(),_BoundHTTPSHandler(context=context))

    def request(self, method, path, body=None, *, deadline=None):
        raw = None if body is None else json.dumps(body,separators=(',',':')).encode()
        req = Request(self.base_url+path,data=raw,method=method,
                      headers={'Authorization':'Bearer '+self._token,'Content-Type':'application/json'})
        end=time.monotonic()+self.request_timeout
        if deadline is not None:end=min(end,deadline)
        budget=None;context_token=None
        try:
            budget=_RequestBudget(end)
            context_token=_ACTIVE_BUDGET.set(budget)
            with self.opener.open(req,timeout=budget.remaining()) as response:
                budget.remaining()
                raw = response.read(1024*1024+1)
                budget.remaining()
                if len(raw)>1024*1024: raise CraftyError('Crafty response too large')
            value = json.loads(raw)
            if not isinstance(value,dict) or value.get('status') != 'ok':
                raise CraftyError('Crafty rejected the request')
            return value.get('data',{})
        except HTTPError as error:
            raise CraftyError('Crafty HTTP request failed with status '+str(error.code)) from None
        except (URLError,TimeoutError,OSError,ValueError,HTTPException):
            # Never retain remote error bodies, URLs, auth headers or arbitrary exception text.
            raise CraftyError('Crafty request failed') from None
        finally:
            if context_token is not None:_ACTIVE_BUDGET.reset(context_token)
            if budget is not None:budget.close()

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

    def stats(self, *, deadline=None):
        data = self.request('GET','/api/v2/servers/'+self.server_id+'/stats',deadline=deadline)
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
            if time.monotonic() >= deadline: raise CraftyError('Crafty stop confirmation timed out')
            value = self.stats(deadline=deadline)
            if time.monotonic() >= deadline: raise CraftyError('Crafty stop confirmation timed out')
            if self._fresh(value,baseline) and value.get('running') is False: return value
            if time.monotonic() >= deadline: raise CraftyError('Crafty stop confirmation timed out')
            time.sleep(min(self.poll_interval,max(0,deadline-time.monotonic())))

    def wait_started(self, baseline):
        deadline = time.monotonic()+self.start_timeout
        while True:
            if time.monotonic() >= deadline: raise CraftyError('Crafty game readiness timed out')
            value = self.stats(deadline=deadline)
            if time.monotonic() >= deadline: raise CraftyError('Crafty game readiness timed out')
            # int_ping_results is a CharField in Crafty 4.11.0; online is player count.
            if (self._fresh(value,baseline) and value.get('running') is True
                    and value.get('int_ping_results') in (True,'True','true')): return value
            if time.monotonic() >= deadline: raise CraftyError('Crafty game readiness timed out')
            time.sleep(min(self.poll_interval,max(0,deadline-time.monotonic())))
