import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from test_agent import FakeZFS
from minecraft_zfs_agent.config import Config
from minecraft_zfs_agent.coordinator import CheckpointService
from minecraft_zfs_agent.http_service import AgentHTTPServer


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.service=CheckpointService(Config('tank/world',root/'world',root/'state',root/'token'),FakeZFS())
        self.token='s'*40
        self.server=AgentHTTPServer(('127.0.0.1',0),self.service,self.token,max_workers=2)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.service.close();self.thread.join();self.tmp.cleanup()
    def request(self,path=' /v1/status',method='GET',body=None,token=True,headers=None):
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        h={'Authorization':'Bearer '+self.token} if token else {}
        h.update(headers or {})
        connection.request(method,path.strip(),body=body,headers=h)
        response=connection.getresponse();data=response.read();connection.close()
        return response.status,json.loads(data)
    def test_all_v1_requires_auth_and_health_discloses_only_readiness(self):
        for path in ['/v1/status','/v1/snapshots','/v1/rollbacks/current']:
            status,value=self.request(path,token=False);self.assertEqual(status,401);self.assertIn('error',value)
        status,value=self.request('/healthz',token=False);self.assertEqual(status,200);self.assertEqual(set(value),{'ready'})
    def test_oversized_duplicate_non_object_invalid_json_rejected(self):
        for raw in [' '*16385,'{"id":"a","id":"b"}','[]','{"x":NaN}','broken']:
            status,value=self.request('/v1/checkpoints','POST',raw)
            self.assertEqual(status,400);self.assertIn('code',value['error'])
    def test_unsupported_phase_path_and_rollback_disabled_fail_closed(self):
        status,_=self.request('/v1/unknown');self.assertEqual(status,404)
        status,_=self.request('/v1/rollbacks','POST','{}');self.assertEqual(status,503)
    def test_full_checkpoint_roundtrip(self):
        import time,uuid
        now=time.time_ns()//1_000_000;id=str(uuid.uuid4())
        status,value=self.request('/v1/checkpoints','POST',json.dumps(dict(id=id,event='manual_save',created_ms=now,deadline_ms=now+15000)))
        self.assertEqual((status,value['state']),(200,'PREPARED'))
        status,value=self.request('/v1/checkpoints/'+id+'/snapshot','POST',json.dumps(dict(frozen_ms=now)))
        self.assertEqual((status,value['state']),(200,'SNAPSHOT_READY'))
        status,value=self.request('/v1/checkpoints/'+id+'/commit','POST',json.dumps(dict(committed_ms=now)))
        self.assertEqual((status,value['state']),(200,'COMMITTED'))
        status,value=self.request('/v1/snapshots');self.assertEqual(value['snapshots'][0]['event'],'manual_save')
    def test_workers_are_bounded_and_excess_socket_gets503(self):
        a=http.client.HTTPConnection(*self.server.server_address);a.connect();a.sock.sendall(b'GET /healthz HTTP/1.1\r\n')
        b=http.client.HTTPConnection(*self.server.server_address);b.connect();b.sock.sendall(b'GET /healthz HTTP/1.1\r\n')
        import time;time.sleep(.1)
        try:status,_=self.request('/healthz',token=False);self.assertEqual(status,503)
        finally:a.close();b.close()

if __name__=='__main__':unittest.main()
