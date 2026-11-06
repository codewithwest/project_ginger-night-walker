"""Native Ollama /api/chat and Strands Decider /v1/systemone adapters."""
import json
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

class ModelError(RuntimeError): pass


def identity_headers(agent, capability, operation, task_id=None, step=None, model=None):
    values = {"X-Ginger-Agent": agent, "X-Ginger-Capability": capability,
              "X-Ginger-Operation": operation, "X-Ginger-Integration": "goal-aware-strands",
              "X-Ginger-Task-Id": task_id, "X-Ginger-Step": step,
              "X-Ginger-Model": model}
    return {key: re.sub(r"[\\r\\n\\x00-\\x1f\\x7f]", " ", str(value).strip())[:200]
            for key, value in values.items() if value is not None and str(value).strip()}


def post_json(url, data, headers=None, timeout=60):
    request = Request(url, json.dumps(data).encode(), headers={"Content-Type":"application/json", **(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as res:
            return json.loads(res.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        detail = ""
        if isinstance(exc, HTTPError):
            detail = exc.read().decode(errors="replace")[:400]
        raise ModelError(f"Could not call {url}: {exc} {detail}") from exc


class ChatModel:
    """Native Ollama endpoint, nonstreaming and configurable thinking."""
    def __init__(self, base_url, model, api_key="local", timeout=60, think=False, agent="goal-state"):
        self.base_url, self.model, self.timeout, self.think = base_url.rstrip("/"), model, timeout, think
        self.api_key, self.agent = api_key, agent
        self.task_id, self.step = None, None

    def set_context(self, task_id=None, step=None):
        self.task_id, self.step = task_id, step

    def generate(self, system, user, max_tokens=300, temperature=0):
        data = {"model":self.model,"messages":[{"role":"system","content":system},
                {"role":"user","content":user}],"stream":False,"think":self.think,
                "options":{"temperature":temperature,"num_predict":max_tokens}}
        headers = identity_headers(self.agent, "goal-tracking" if self.agent == "goal-state" else "tool-argument-generation", "model-generate", self.task_id, self.step, self.model)
        if self.api_key and self.api_key != "local":
            headers["Authorization"] = "Bearer " + self.api_key
        response = post_json(f"{self.base_url}/api/chat",data,headers=headers,timeout=self.timeout)
        content = response.get("message",{}).get("content")
        if not isinstance(content,str):
            raise ModelError(f"Unexpected Ollama response: {str(response)[:300]}")
        return content


ACTION_DESCRIPTIONS = {
    "navigate":"Open a known website URL or user-provided link, usually from the original goal.",
    "read_page":"Get the current page snapshot to see elements and content.",
    "click":"Click a currently visible page element to advance the remaining goal.",
    "type_text":"Type text into a grounded field/input.",
    "select_option":"Select a value in a known dropdown.",
    "extract_text":"Read or extract requested information visible on the page.",
    "finish":"All user success criteria are satisfied by actual observed evidence.",
    "blocked":"The task cannot proceed due to a real blocking condition.",
}

class StrandsDecider:
    """Official Strands Decider /v1/systemone typed choice-question protocol."""
    def __init__(self, base_url, path="/v1/systemone", timeout=60):
        self.url = base_url.rstrip("/") + "/" + path.lstrip("/")
        self.timeout = timeout
        self.task_id, self.step = None, None

    def set_context(self, task_id=None, step=None):
        self.task_id, self.step = task_id, step

    def choose(self, goal, state, actions):
        state_payload = {"original_goal":goal, "current_state":state}
        criteria = {name:ACTION_DESCRIPTIONS.get(name,name) for name in actions}
        payload = {"state":json.dumps(state_payload,ensure_ascii=False),
                   "questions":{"next_action":{"type":"choice",
                       "instructions":"Choose exactly one NEXT action that advances the user's original goal using current browser evidence. Do not finish before all success criteria have supporting observations. Prefer read_page if browser state is unknown. Avoid repeating ineffective actions.",
                       "criteria":criteria}}}
        headers = identity_headers("strands-decider", "action-selection", "choose-next-action", self.task_id, self.step)
        result = post_json(self.url,payload,headers=headers,timeout=self.timeout)
        answer = result.get("answers",{}).get("next_action",{})
        action = answer.get("choice")
        probs = answer.get("probabilities") or {}
        if not action and probs:
            action = max(probs,key=probs.get)
        if action not in actions:
            raise ModelError(f"Unexpected Strands answer: {str(result)[:500]}")
        return {"action":action,"confidence":answer.get("confidence"),
                "probabilities":probs,"model":result.get("model"),"raw":result}
