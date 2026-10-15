"""Opt-in live Docker smoke test: fresh MongoDB, app, private network and preview."""
import json
import tempfile
import urllib.request
import time
from pathlib import Path
from judecode.agent.sandbox_first import SandboxFirst
with tempfile.TemporaryDirectory(prefix='jude-system-source-') as root:
    Path(root,'original.txt').write_text('unchanged')
    env=SandboxFirst(root)
    try:
        result=env.start('python:3.12-slim',services=[{'name':'db','image':'mongo:4.4','command':'mongod --bind_ip_all'}])
        print(json.dumps(result),flush=True)
        print(env.python_packages(['pymongo']),flush=True)
        program='''from http.server import BaseHTTPRequestHandler, HTTPServer
from pymongo import MongoClient
import socket
client=MongoClient('mongodb://db:27017',serverSelectionTimeoutMS=10000)
db=client.sandbox_only
db.items.insert_one({'value':'test-data'})
assert db.items.count_documents({}) == 1
assert not __import__('pathlib').Path('/var/run/docker.sock').exists()
assert not any(line.split()[1] == '00000000' for line in open('/proc/net/route').read().splitlines()[1:])
for host in ['1.1.1.1', '10.0.0.1','host.docker.internal']:
    try:
        s=socket.create_connection((host,80),timeout=1)
    except OSError: pass
    else:
        s.close();raise AssertionError('External connectivity: '+host)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.end_headers()
        self.wfile.write(str(db.items.count_documents({})).encode())
HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
'''
        env.mapped('server.py').write_text(program)
        print(env.run('python server.py > /tmp/app.log 2>&1 &'),flush=True)
        url=env.preview(8000)['url']
        for _ in range(40):
            try:
                body=urllib.request.urlopen(url,timeout=2).read()
                assert body==b'1',body
                break
            except OSError:time.sleep(.5)
        else: raise AssertionError(env.run('cat /tmp/app.log'))
        print('PASS full system: API -> Mongo insert/read -> localhost HTTP; external/LAN blocked',flush=True)
        assert Path(root,'original.txt').read_text()=='unchanged'
        assert not Path(root,'server.py').exists()
    finally:
        print(env.stop(),flush=True)
