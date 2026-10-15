"""Session-owned project contract; success requires executed acceptance checks."""
import copy
import json
import re
import time
from pathlib import Path

PROMPT = """
For an authorized multi-feature implementation, call project_plan BEFORE implementation.
Translate ALL agreed features into stable IDs, acceptance criteria and real executable
checks. Include integration and browser E2E checks for web UI, not just build/curl.
Do not use this for greetings, explanations or read-only questions. The contract is
preserved across turns; follow-up requests do not replace its original goal. Work on
pending features, run project_verify, repair failures, and continue until all pass.
Checks must assert the requested behavior; never replace a failing check with a weaker
one, echo/true, or mark a scaffold complete. project_verify runs in project root;
use explicit remote commands for remote deployments. Never execute outside user scope.
Use project_pause with an honest reason only when blocked or needing user input.
Use expert_help when repeated fixes do not progress. Final claims must match evidence.
Before coding, map each requested user journey to observable acceptance behavior.
Cover persistence/reload, invalid input and relevant permissions as well as the happy
path. Implement one vertical slice through UI/API/storage before expanding it.
Write checks from requirements, not by copying the implementation's assumptions.
Before delivery, compare the original request against the contract for omitted scope;
append missing features. A passing build or an empty test suite is not product proof.
Report what was actually exercised and what remains untested; do not claim 100% quality.
"""


def _empty_test_run(output):
    # These are runner summaries, not arbitrary mentions of "0 tests" in logs.
    return bool(re.search(r'(?m)^\s*(?:Ran 0 tests in [^\n]+|'
                          r'No tests found[.!]?|=+ no tests ran in [^\n]+)\s*$', output))


def definition(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties, 'required': required,
                       'additionalProperties': False}}}

DEFINITIONS = [
    definition('project_plan', 'Create a persistent implementation contract, or append new features. Existing criteria cannot be overwritten.', {
        'goal': {'type': 'string'}, 'root': {'type': 'string'},
        'features': {'type': 'array', 'minItems': 1, 'items': {'type': 'object',
            'properties': {'id': {'type': 'string'}, 'criteria': {'type': 'string'},
                           'checks': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}},
            'required': ['id', 'criteria', 'checks'], 'additionalProperties': False}}}, ['goal', 'root', 'features']),
    definition('project_revise_checks', 'Repair a broken verification invocation without weakening acceptance criteria. Record why the old command was wrong; preserve test coverage.',
        {'id': {'type': 'string'}, 'checks': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}, 'reason': {'type': 'string'}}, ['id', 'checks', 'reason']),
    definition('project_verify', 'Execute ALL contract checks; only actual exit code zero passes. Inspect failures and repair before finishing.', {}, []),
    definition('project_resolve_failure', 'Resolve a historical tool error only AFTER all project checks pass. Explain evidence; never hide an unresolved failure.',
        {'failed_tool': {'type': 'string'}, 'failed_arguments': {'type': 'string'}, 'reason': {'type': 'string'}},
        ['failed_tool', 'failed_arguments', 'reason']),
    definition('project_pause', 'Pause an incomplete project for a concrete blocker or required user input; never claim completion.',
               {'reason': {'type': 'string'}}, ['reason']),
]
NAMES = {d['function']['name'] for d in DEFINITIONS}


class ProjectContract:
    def __init__(self, state=None):
        if state is not None:
            if (not isinstance(state, dict) or set(state) != {'goal', 'root', 'features', 'paused'} or
                    any(not isinstance(state[k], str) for k in ('goal', 'root', 'paused')) or
                    not isinstance(state['features'], dict) or not state['features']):
                raise ValueError('Invalid project snapshot')
            for identifier, feature in state['features'].items():
                if (not isinstance(feature, dict) or feature.get('id') != identifier or
                        not isinstance(feature.get('criteria'), str) or
                        type(feature.get('passed')) is not bool or
                        not isinstance(feature.get('checks'), list) or not feature['checks'] or
                        any(not isinstance(c, str) or not c.strip() for c in feature['checks']) or
                        not isinstance(feature.get('evidence'), list)):
                    raise ValueError('Invalid project feature snapshot')
                if feature['passed'] and (len(feature['evidence']) != len(feature['checks']) or
                        any(not isinstance(e, dict) or e.get('exit_code') != 0 or e.get('validation_error') or e.get('command') != c
                            for e, c in zip(feature['evidence'], feature['checks']))):
                    raise ValueError('Project success is missing execution evidence')
        self.state = copy.deepcopy(state)
        self.idle_rounds = 0
        self.verify_rounds = 0

    def plan(self, goal, root, features):
        # Some OpenAI-compatible providers double-encode nested tool arguments.
        # Decode exactly once with the same duplicate-key/non-finite rejection.
        if isinstance(features, str):
            from judecode.agent.tool_history import parse_tool_arguments
            features = parse_tool_arguments('{"features":' + features + '}')["features"]
        root = str(Path(root).expanduser().resolve())
        if not isinstance(goal, str) or not goal.strip() or not Path(root).is_dir():
            raise ValueError('A goal and existing project root are required')
        if not isinstance(features, list) or not 1 <= len(features) <= 100:
            raise ValueError('features must be a JSON array of 1..100 objects, not a string. Example: [{"id":"sale","criteria":"sale reduces stock","checks":["python3 -m unittest test_inventory.TestSell"]}]')
        prepared = {}
        for f in features:
            if (not isinstance(f, dict) or set(f) != {'id', 'criteria', 'checks'} or
                    any(not isinstance(f[k], str) or not f[k].strip() for k in ('id', 'criteria')) or
                    not isinstance(f['checks'], list) or not 1 <= len(f['checks']) <= 20 or
                    any(not isinstance(c, str) or not c.strip() for c in f['checks'])):
                raise ValueError('Each feature requires id, criteria, and executable checks')
            if f['id'] in prepared:
                raise ValueError('Duplicate feature ID')
            prepared[f['id']] = {**copy.deepcopy(f), 'passed': False, 'evidence': []}
        if self.state:
            if self.state['root'] != root or self.state['goal'] != goal:
                raise ValueError('Existing goal/root are immutable; use a new conversation for another project')
            if set(prepared) & set(self.state['features']):
                raise ValueError('Cannot overwrite existing acceptance criteria')
            self.state['features'].update(prepared)
        else:
            self.state = {'goal': goal, 'root': root, 'features': prepared, 'paused': ''}
        self.state['paused'] = ''
        return self.summary()

    def invalidate(self):
        if self.state:
            for f in self.state['features'].values():
                f['passed'] = False

    def summary(self):
        if not self.state:
            return 'No project contract'
        return json.dumps(self.state, ensure_ascii=False)

    @property
    def complete(self):
        return bool(self.state and all(f['passed'] for f in self.state['features'].values()))

    def verify(self, run, cancelled):
        if not self.state:
            raise ValueError('Create project_plan first')
        self.verify_rounds += 1
        if self.verify_rounds > 12:
            self.state['paused'] = 'Verification retry limit reached; inspect evidence and change approach.'
            return self.summary()
        previous_passed = sum(f["passed"] for f in self.state["features"].values())
        deadline = time.monotonic() + 300
        self.invalidate()
        for f in self.state['features'].values():
            evidence = []
            for command in f['checks']:
                if cancelled() or time.monotonic() >= deadline:
                    return self.summary()
                result = run(command, self.state['root'])
                output = str(result.get('stdout', '')) + '\n' + str(result.get('stderr', ''))
                evidence.append({'command': command, 'exit_code': result.get('exit_code'),
                                 'output': output[-2000:],
                                 'validation_error': 'Test runner executed no tests' if _empty_test_run(output) else ''})
            f['evidence'] = evidence
            f['passed'] = bool(evidence) and all(e['exit_code'] == 0 and not e['validation_error'] for e in evidence)
        if sum(f["passed"] for f in self.state["features"].values()) > previous_passed:
            self.idle_rounds = 0
        return self.summary()

    def gate(self):
        if not self.state or self.complete:
            return None
        if self.state['paused']:
            return False
        self.idle_rounds += 1
        if self.idle_rounds > 3:
            self.state['paused'] = 'No verified progress after three continuation requests.'
            return False
        pending = [f['id'] for f in self.state['features'].values() if not f['passed']]
        return ('Project is NOT complete. Continue implementing and run project_verify. '
                'Do not merely summarize. If blocked, use project_pause with the specific reason. '
                'Pending acceptance checks: ' + ', '.join(pending))
