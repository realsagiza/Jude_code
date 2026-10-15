"""Opt-in Docker build and approved release to a disposable LOCAL target; no production access."""
import json, shlex, tempfile
from pathlib import Path
from judecode.agent.sandbox_first import SandboxFirst

with tempfile.TemporaryDirectory(prefix='jude-release-smoke-') as folder:
    base = Path(folder)
    source = base / 'ระบบ ทดสอบ'
    source.mkdir()
    target = base / 'local-release.txt'
    (source / 'release.sh').write_text('set -eu\ncp artifact.txt ' + shlex.quote(str(target)) + '\ncmp artifact.txt ' + shlex.quote(str(target)) + '\n')
    sandbox = SandboxFirst(source)
    try:
        sandbox.start('python:3.12-slim', [])
        built = sandbox.run("python -c \"from pathlib import Path; Path('artifact.txt').write_text('built inside isolated Docker'); assert Path('artifact.txt').read_text() == 'built inside isolated Docker'\"")
        assert built['exit_code'] == 0, built
        plan = sandbox.prepare('cd /workspace && bash release.sh', str(target), 'remove disposable local target', True, required_commands=['bash', 'cp', 'cmp'])
        assert not target.exists()
        result = sandbox.release('deploy แผนนี้', True, True)
        assert result['exit_code'] == 0, result
        assert target.read_text() == 'built inside isolated Docker'
        assert not (source / 'artifact.txt').exists()
        assert sandbox.pending is None
        print(json.dumps({'docker_build': 'passed', 'explicit_release_to_local_target': 'passed', 'original_source': 'unchanged', 'production_contacted': False}))
    finally:
        sandbox.stop()
