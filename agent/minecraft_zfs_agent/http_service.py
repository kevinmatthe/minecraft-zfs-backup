"""Authenticated bounded HTTP transport; no game commands or request-selected paths."""
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import logging
import socket
import threading
from urllib.parse import urlsplit
from .protocol import APIError,MAX_MESSAGE,parse_json,object_fields,identifier
from .zfs import OWNED


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads=False
    block_on_close=True
    request_queue_size=16
    def __init__(self,address,service,token,rollback=None,max_workers=8):
        self.service,self.token,self.rollback=service,token.encode('utf-8'),rollback
        self.workers=threading.BoundedSemaphore(max_workers)
        super().__init__(address,Handler)
    def process_request(self,request,address):
        if not self.workers.acquire(blocking=False):
            raw=b'{"error":{"code":"busy","message":"HTTP concurrency limit reached"}}'
            try:request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: '+str(len(raw)).encode()+b'\r\nConnection: close\r\n\r\n'+raw)
            except OSError:pass
            finally:self.shutdown_request(request)
            return
        try:super().process_request(request,address)
        except BaseException:self.workers.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.workers.release()
    def handle_error(self,*_):logging.error('HTTP connection failed')


class Handler(BaseHTTPRequestHandler):
    server_version='MinecraftZFS/1'
    sys_version=''
    def setup(self):
        self.request.settimeout(3)
        super().setup()
    def log_message(self,*_):pass
    def reply(self,status,value):
        raw=json.dumps(value,separators=(',',':'),allow_nan=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('Connection','close');self.end_headers();self.wfile.write(raw);self.close_connection=True
    def body(self):
        if self.headers.get('Transfer-Encoding') is not None:raise APIError(400,'invalid_encoding','Chunked requests are unsupported')
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)!=1 or not lengths[0].isdigit():raise APIError(400,'invalid_length','A single Content-Length is required')
        length=int(lengths[0])
        if length>MAX_MESSAGE:raise APIError(400,'body_too_large','JSON request exceeds16KiB')
        raw=self.rfile.read(length)
        if len(raw)!=length:raise APIError(400,'incomplete_body','Incomplete request body')
        return parse_json(raw)
    def authorized(self):
        values=self.headers.get_all('Authorization',[])
        return len(values)==1 and hmac.compare_digest(values[0].encode('utf-8'),b'Bearer '+self.server.token)
    def route(self,method):
        parsed=urlsplit(self.path);path=parsed.path
        if parsed.query or parsed.fragment:raise APIError(400,'invalid_path','Query parameters are unsupported')
        if path=='/healthz' and method=='GET':
            ready=self.server.service.status()['ready'];return (200 if ready else 503),dict(ready=ready)
        if not path.startswith('/v1/'):raise APIError(404,'not_found','Unknown endpoint')
        if not self.authorized():raise APIError(401,'unauthorized','Bearer token required')
        service=self.server.service
        if method=='GET':
            if path=='/v1/status':return 200,service.status()
            if path=='/v1/snapshots':return 200,service.list_snapshots()
            if path.startswith('/v1/rollbacks/'):
                manager=self.server.rollback
                if manager is None:raise APIError(503,'rollback_disabled','Crafty rollback is not configured')
                id=path[len('/v1/rollbacks/'):]
                if id!='current':identifier(id)
                value=manager.current() if id=='current' else manager.get(id)
                if value is None:raise APIError(404,'unknown_rollback','Rollback job not found')
                return 200,value
        if method=='POST':
            body=self.body()
            if path=='/v1/checkpoints':return 200,service.prepare(body)
            if path=='/v1/lifecycle':return 200,service.lifecycle(body)
            pieces=path.strip('/').split('/')
            if len(pieces)==4 and pieces[:2]==['v1','checkpoints'] and pieces[3] in ('snapshot','commit','cancel'):
                return 200,getattr(service,pieces[3])(pieces[2],body)
            if path=='/v1/rollbacks':
                if self.server.rollback is None:raise APIError(503,'rollback_disabled','Crafty rollback is not configured')
                object_fields(body,{'id','snapshot'});identifier(body['id'])
                if not isinstance(body['snapshot'],str) or not OWNED.fullmatch(body['snapshot']):raise APIError(400,'invalid_snapshot','Managed published snapshot required')
                return 202,self.server.rollback.submit(body['id'],body['snapshot'])
        raise APIError(404,'not_found','Unknown endpoint')
    def handle_method(self,method):
        try:status,value=self.route(method)
        except APIError as error:
            status=error.status;value=dict(error=dict(code=error.code,message=error.message))
            if status in (409,410,503) and self.authorized():
                with self.server.service.lock:
                    self.server.service.ledger['last_error']=dict(code=error.code,message=error.message,updated_ms=self.server.service.clock())
                    self.server.service.save()
        except (socket.timeout,TimeoutError):status,value=400,dict(error=dict(code='read_timeout',message='Request read timed out'))
        except Exception as error:
            logging.error('HTTP operation failed (%s)',type(error).__name__)
            status,value=500,dict(error=dict(code='internal_error',message='Operation failed; inspect administrator state'))
        try:self.reply(status,value)
        except (BrokenPipeError,ConnectionResetError,socket.timeout):pass
    def do_GET(self):self.handle_method('GET')
    def do_POST(self):self.handle_method('POST')

    def do_PUT(self):self.handle_method('PUT')
    def do_DELETE(self):self.handle_method('DELETE')
    def do_PATCH(self):self.handle_method('PATCH')
    def do_OPTIONS(self):self.handle_method('OPTIONS')
