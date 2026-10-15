"""Drive the real configured Jude agent, retaining transcripts and explicit approvals.

No mocked model/tools and no auto-approval. Give a task file; after each turn stdin
accepts the next human/reviewer message. Use EXIT to leave; /file PATH sends a saved
review brief. Project changes remain in Jude's private sandbox.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from judecode.config import SYSTEM_PROMPT
from judecode.api import create_api_client
from judecode.agent.engine import AgentEngine

async def run(args):
    source = Path(args.source).resolve()
    report = Path(args.report).resolve()
    message = Path(args.task).resolve().read_text()
    os.chdir(source)
    client = create_api_client()
    agent = AgentEngine(SYSTEM_PROMPT, client)
    if args.resume:
        agent.resume_session(args.resume)
    try:
        while message:
            await agent.chat(message, trusted_user=True)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({
                'session': agent.autonomous.session.session_id,
                'status': agent.autonomous.session.status,
                'reason': agent.autonomous.session.outcome_reason,
                'workspace': str(agent.isolation.workspace),
                'pending': agent.isolation.pending, 'project': agent.project.state,
                'last_response': agent.messages[-1].get('content', '')},
                ensure_ascii=False, indent=2))
            print('ACCEPTANCE_WAITING ' + str(report), flush=True)
            message = await asyncio.to_thread(input)
            if message == 'EXIT':
                break
            if message.startswith('/file '):
                message = Path(message[6:]).read_text()
    finally:
        await client.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--resume')
    asyncio.run(run(parser.parse_args()))
