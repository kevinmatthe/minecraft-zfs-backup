import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from minecraft_zfs_agent.crafty import CraftyClient, CraftyError
except ImportError:
    CraftyClient = CraftyError = None


class CraftyTests(unittest.TestCase):
    def client(self,responses):
        self.assertIsNotNone(CraftyClient,'Crafty integration must exist')
        class Fake(CraftyClient):
            def request(this,method,path,body=None,*,deadline=None):
                this.calls.append((method,path,body))
                result=responses.pop(0)
                if isinstance(result,Exception): raise result
                return result
        c=object.__new__(Fake);c.calls=[];c.server_id='test-server';c.stop_timeout=0.02;c.start_timeout=0.02
        c.poll_interval=0.001
        return c

    def test_disable_policy_verifies_both_flags(self):
        c=self.client([{}, {'auto_start':False,'crash_detection':False}])
        c.set_policy({'auto_start':False,'crash_detection':False})
        self.assertEqual(c.calls[0],('PATCH','/api/v2/servers/test-server',{'auto_start':False,'crash_detection':False}))
        c=self.client([{}, {'auto_start':False,'crash_detection':True}])
        with self.assertRaises(CraftyError): c.set_policy({'auto_start':False,'crash_detection':False})

    def test_stopped_requires_fresh_id_created_and_running_false_not_online(self):
        base={'stats_id':4,'created':'2026-10-19T12:00:00','running':True}
        c=self.client([
            {'stats_id':4,'created':base['created'],'running':False,'online':0},
            {'stats_id':5,'created':'2026-10-19T12:00:01','running':True,'online':0},
            {'stats_id':6,'created':'2026-10-19T12:00:02','running':False,'online':0},
        ])
        value=c.wait_stopped(base)
        self.assertEqual(value['stats_id'],6)

    def test_start_requires_process_and_game_readiness(self):
        base={'stats_id':4,'created':'2026-10-19T12:00:00','running':False}
        c=self.client([
            {'stats_id':5,'created':'2026-10-19T12:00:01','running':True,'int_ping_results':'False'},
            {'stats_id':6,'created':'2026-10-19T12:00:02','running':True,'int_ping_results':'True'},
        ])
        self.assertEqual(c.wait_started(base)['stats_id'],6)

    def test_transport_error_never_exposes_token_or_body(self):
        self.assertIsNotNone(CraftyClient)
        from types import SimpleNamespace
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as folder:
            token=Path(folder)/'token';token.write_text('secret-value-that-must-not-ever-appear');token.chmod(0o600)
            c=CraftyClient(SimpleNamespace(base_url='https://crafty:8443',server_id='test-server',token_file=token,
                verify_tls=True,ca_file=None,stop_timeout_seconds=1,start_timeout_seconds=1))
            with patch.object(c.opener,'open',side_effect=HTTPError('https://crafty:8443',403,'SECRET BODY',{},None)):
                with self.assertRaises(CraftyError) as error:c.settings()
            self.assertNotIn('secret',str(error.exception).lower())
            self.assertNotIn('SECRET BODY',str(error.exception))

    def test_https_handler_does_not_pass_removed_python313_argument(self):
        import ssl
        from minecraft_zfs_agent.crafty import _BoundHTTPSHandler
        handler=_BoundHTTPSHandler(context=ssl.create_default_context())
        # Python 3.13 removed both this handler member and the connection kwarg.
        if hasattr(handler,'_check_hostname'):del handler._check_hostname
        with patch.object(handler,'do_open',return_value='response') as opened:
            self.assertEqual(handler.https_open(object()),'response')
        self.assertNotIn('check_hostname',opened.call_args.kwargs)

    def test_actual_tls_request_uses_configured_context(self):
        import http.server
        import ssl
        import subprocess
        import threading
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);cert=folder/'cert.pem';key=folder/'key.pem'
            import os
            fixture=os.environ.get('CRAFTY_TEST_TLS_FIXTURE')
            if fixture:
                cert=Path(fixture)/'cert.pem';key=Path(fixture)/'key.pem'
            else:
                subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                    '-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost',
                    '-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
            token=folder/'token';token.write_text('test-token-value-that-is-long-enough');token.chmod(0o600)
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(this):
                    body=b'{"status":"ok","data":{"auto_start":false,"crash_detection":false}}'
                    this.send_response(200);this.send_header('Content-Length',str(len(body)));this.end_headers();this.wfile.write(body)
                def log_message(this,*_):pass
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
            server.socket=context.wrap_socket(server.socket,server_side=True)
            worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.01},daemon=True);worker.start()
            try:
                c=CraftyClient(SimpleNamespace(base_url='https://localhost:'+str(server.server_address[1]),server_id='test',token_file=token,
                    verify_tls=True,ca_file=cert,stop_timeout_seconds=1,start_timeout_seconds=1))
                self.assertEqual(c.settings(),{'auto_start':False,'crash_detection':False})
            finally:server.shutdown();server.server_close();worker.join(1)

    def test_slow_drip_headers_and_body_obey_absolute_request_deadline(self):
        import socketserver
        import threading
        import time
        from types import SimpleNamespace
        for mode in ('headers','body','stop_budget','start_budget'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                token=Path(folder)/'token';token.write_text('test-token-value-that-is-long-enough');token.chmod(0o600)
                class Drip(socketserver.BaseRequestHandler):
                    def handle(this):
                        this.request.settimeout(1)
                        data=b''
                        while b'\r\n\r\n' not in data:
                            data+=this.request.recv(4096)
                        body=b'{"status":"ok","data":{}}'
                        header=b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'
                        try:
                            if mode=='headers':
                                for byte in header:
                                    this.request.sendall(bytes([byte]));time.sleep(0.02)
                                this.request.sendall(body)
                            else:
                                this.request.sendall(header)
                                for byte in body:
                                    this.request.sendall(bytes([byte]));time.sleep(0.02)
                        except OSError:pass
                class Server(socketserver.ThreadingTCPServer):
                    allow_reuse_address=True
                    daemon_threads=True
                server=Server(('127.0.0.1',0),Drip)
                worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.01},daemon=True);worker.start()
                try:
                    c=CraftyClient(SimpleNamespace(base_url='http://127.0.0.1:'+str(server.server_address[1]),server_id='test',token_file=token,
                        verify_tls=True,ca_file=None,stop_timeout_seconds=0.12,start_timeout_seconds=0.12))
                    c.request_timeout=0.12 if mode in ('headers','body') else 10
                    start=time.monotonic()
                    with self.assertRaises(CraftyError):
                        if mode=='stop_budget':c.wait_stopped({'stats_id':1,'created':'2026-01-01T00:00:00','running':True})
                        elif mode=='start_budget':c.wait_started({'stats_id':1,'created':'2026-01-01T00:00:00','running':False})
                        else:c.request('GET','/stats')
                    elapsed=time.monotonic()-start
                    self.assertLess(elapsed,0.35,'dripping bytes must not extend elapsed budget')
                finally:
                    server.shutdown();server.server_close();worker.join(1)

if __name__ == '__main__': unittest.main()
