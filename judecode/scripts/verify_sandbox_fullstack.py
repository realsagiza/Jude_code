"""Opt-in live full-stack smoke: Node frontend, Python API, MongoDB, isolated preview."""
import tempfile,time,urllib.request
from pathlib import Path
from judecode.agent.sandbox_first import SandboxFirst
with tempfile.TemporaryDirectory() as root:
 env=SandboxFirst(root)
 try:
  env.start('python:3.12-slim',services=[{'name':'db','image':'mongo:4.4','command':'mongod --bind_ip_all'},
   {'name':'frontend','image':'node:20-alpine','workspace':True,'command':'while :; do sleep 3600; done'}])
  env.python_packages(['pymongo'])
  env.node_packages(['express@4.21.2'],service='frontend')
  env.mapped('api.py').write_text('''from http.server import BaseHTTPRequestHandler,HTTPServer
from pymongo import MongoClient
c=MongoClient('mongodb://db:27017',serverSelectionTimeoutMS=10000)
c.sandbox_only.items.insert_one({'value':'polyglot-test'})
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(str(c.sandbox_only.items.count_documents({})).encode())
HTTPServer(('0.0.0.0',8000),H).serve_forever()
''')
  env.mapped('frontend.js').write_text("const express=require('express');const app=express();app.get('/',async(req,res)=>{try{const r=await fetch('http://app:8000');res.send('<h1>Sandbox items: '+await r.text()+'</h1>')}catch(e){res.status(503).send('waiting')}});app.listen(3000,'0.0.0.0');")
  assert env.run('python api.py >/tmp/api.log 2>&1 &')['exit_code']==0
  assert env.run('node frontend.js >/tmp/front.log 2>&1 &',service='frontend')['exit_code']==0
  url=env.preview(3000,service='frontend')['url']
  for _ in range(40):
   try:
    response=urllib.request.urlopen(url,timeout=2)
    body=response.read()
    assert b'Sandbox items: 1' in body,body
    assert "connect-src 'self'" in response.headers['Content-Security-Policy']
    break
   except OSError:time.sleep(.5)
  else:raise AssertionError(env.run('cat /tmp/api.log'))
  assert not Path(root,'frontend.js').exists()
  print('PASS: Node20/Express frontend -> Python API -> MongoDB -> localhost preview; source unchanged',flush=True)
 finally:env.stop()
