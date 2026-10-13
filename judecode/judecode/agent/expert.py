"""Bounded, sequential expert handoff using the existing engine and safeguards."""
from judecode import config
from judecode.api.client import ApiClient

ROUTING_PROMPT = """
Expert routing: Start each new user request with the primary model. Handle ordinary
work yourself. If a coding task requires difficult diagnosis, substantial multi-file
architecture changes, or repeated unsuccessful fixes, call expert_help early with
reason, goal, constraints and acceptance_checks. Do not wait to fail first when the
task is clearly hard. This hands the SAME conversation/tools to the configured
expert, not a parallel worker. Never delegate beyond the user's authorization.
When acting as the expert, inspect the existing work, implement and run appropriate
checks. Keep working within the remaining limits; do not repeat uncertain actions.
Call expert_return with an evidence-based summary, remaining work and limitations
when ready to hand back. The primary model must verify/report that evidence, never
convert an incomplete or unverified result into a success claim.
"""


def definition(name, description, fields):
    return {"type": "function", "function": {"name": name,
        "description": description, "parameters": {"type": "object",
        "properties": {key: {"type": "string"} for key in fields},
        "required": fields, "additionalProperties": False}}}

HELP = definition("expert_help", "Hand this difficult coding task to the configured expert, with the same tools and history. Call alone, not with other tools.",
                  ["reason", "goal", "constraints", "acceptance_checks"])
RETURN = definition("expert_return", "Return control to the primary model. Report actual checks, unresolved issues and next steps. Call alone.", ["summary"])
NAMES = {"expert_help", "expert_return"}


class ExpertRouting:
    def __init__(self):
        self.active = False
        self.turns = 0
        self.handoffs = 0
        self.model = config.EXPERT_MODEL
        self.max_turns = config.EXPERT_MAX_TURNS
        self.max_handoffs = config.EXPERT_MAX_HANDOFFS

    @property
    def available(self):
        return config.EXPERT_ENABLED and bool(config.OPENROUTER_API_KEY)

    def tools(self, base):
        result = list(base)
        if self.available:
            result.append(RETURN if self.active else HELP)
        return result

    def execute(self, name, args):
        if not self.available:
            return "Error executing tool: expert requires enabled routing and an OpenRouter key"
        fields = ["summary"] if name == "expert_return" else ["reason", "goal", "constraints", "acceptance_checks"]
        if set(args) != set(fields) or any(not isinstance(args[k], str) or not args[k].strip() for k in fields):
            return "Error executing tool: all routing fields must be nonempty strings"
        if name == "expert_return":
            if not self.active:
                return "Error executing tool: expert is not active"
            self.active = False
            return "Control returned to primary model. Expert report (verify evidence):\n" + args["summary"]
        if self.active:
            return "Error executing tool: nested expert delegation is disabled"
        if self.handoffs >= self.max_handoffs or self.turns >= self.max_turns:
            return "Error executing tool: expert limit reached; report remaining work, do not retry delegation"
        self.active = True
        self.handoffs += 1
        return (f"Expert {self.model} now owns this task with the same history, tools and safeguards. "
                f"At most {self.max_turns - self.turns} expert requests remain. "
                "Inspect state, implement, verify, then call expert_return. "
                "The handoff arguments above are your brief; original user constraints still apply.")

    async def completion(self, primary, messages, tools):
        if not self.active:
            async for chunk in primary.chat_completion(messages, tools=tools):
                yield chunk
            return
        if self.turns >= self.max_turns:
            yield ApiClient._error_chunk("Expert request limit reached. Work may be incomplete; inspect saved progress before continuing.")
            return
        self.turns += 1
        client = ApiClient(base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY, model=self.model,
            fallback_model=self.model, max_tokens=config.EXPERT_MAX_TOKENS,
            temperature=0.3, max_retries=1)
        try:
            async for chunk in client.chat_completion(messages, tools=tools):
                yield chunk
        finally:
            await client.close()

    def snapshot(self):
        return {"active": self.active, "turns": self.turns,
                "handoffs": self.handoffs, "model": self.model}

    def restore(self, data):
        if not isinstance(data, dict):
            return
        for key in ("turns", "handoffs"):
            value = data.get(key, 0)
            if type(value) is not int or value < 0:
                raise ValueError("Invalid saved expert routing state")
            setattr(self, key, value)
        # Never accept arbitrary saved model IDs as authority to change spending.
        self.active = data.get("active") is True and data.get("model") == self.model and self.available
