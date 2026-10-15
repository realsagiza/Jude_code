"""Opt-in local integration: Docker sandbox -> reviewed host release -> HTTP proof.
Uses the configured model; touches only temporary fixture files and own container.
"""
import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from judecode.api.client import ApiClient
from judecode.agent.engine import AgentEngine


async def main():
    original = Path.cwd()
    client = ApiClient(max_tokens=4096, temperature=0, max_retries=1)
    agent = None
    with tempfile.TemporaryDirectory(prefix='jude-host-release-') as folder:
        base = Path(folder)
        source, target = base / 'source', base / 'target'
        source.mkdir(); target.mkdir()
        (source / 'app.txt').write_text('new release\n')
        (target / 'app.txt').write_text('old release\n')
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(target)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/app.txt'
        script = f'''set -eu
cp '{target}/app.txt' '{target}/app.txt.bak'
cp app.txt '{target}/app.txt'
if ! curl --fail --silent '{url}' | grep -qx 'new release'; then
  cp '{target}/app.txt.bak' '{target}/app.txt'
  exit 1
fi
printf 'HTTP health passed: new release\\n'
'''
        (source / 'release.sh').write_text(script)
        try:
            os.chdir(source)
            agent = AgentEngine('Verify only this local fixture.', client)
            agent.autonomous.enabled = False
            root = agent.isolation
            root.start('python:3.12-slim', [])
            agent.project.plan('Verify fixture artifact', str(root.workspace), [
                {'id': 'artifact', 'criteria': 'artifact content is new release',
                 'checks': ['python -c "from pathlib import Path; assert Path(\'app.txt\').read_text() == \'new release\\n\'; print(\'artifact assertion passed\')"']}])
            agent.project.verify(lambda command, cwd: root.run(command, cwd=cwd), lambda: False)
            assert agent.project.complete, agent.project.summary()
            agent._host_turn = True
            agent._human_requests = [f'ทดสอบ app.txt ใน sandbox แล้วเอาไฟล์ขึ้นปลายทางจำลอง {target} ให้เลย '
                                     f'เก็บ app.txt.bak ไว้และตรวจ HTTP {url} ให้ได้ new release ด้วย ไม่ต้องถามย้ำ']
            inspected = json.loads(await agent._inspect_host({'command': f'curl --fail --silent {url}'}))
            assert inspected.get('exit_code') == 0, inspected
            assert agent.project.complete
            root.prepare('bash release.sh', str(target), 'restore app.txt.bak', agent.project.complete,
                         required_commands=['bash', 'cp', 'curl'], timeout=30)
            result = await agent._release_requested_plan()
            assert result and result.get('exit_code') == 0, (result, agent._host_denial)
            assert (target / 'app.txt').read_text() == 'new release\n'
            assert (target / 'app.txt.bak').read_text() == 'old release\n'
            assert (source / 'app.txt').read_text() == 'new release\n'
            print(json.dumps({'sandbox_tests': True, 'host_inspection': True,
                              'natural_request_deployed': True, 'backup_preserved': True,
                              'http_health': True, 'real_production_touched': False}))
        finally:
            if agent:
                agent.isolation.stop()
                shutil.rmtree(agent.isolation.base, ignore_errors=True)
            os.chdir(original)
            server.shutdown(); server.server_close()
            await client.close()

if __name__ == '__main__':
    asyncio.run(main())
