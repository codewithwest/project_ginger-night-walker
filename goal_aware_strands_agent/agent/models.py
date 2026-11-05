"""Native Ollama /api/chat and Strands Decider /v1/systemone adapters."""
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

class ModelError(RuntimeError): pass


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
    def __init__(self, base_url, model, api_key="local", timeout=60, think=False):
        self.base_url, self.model, self.timeout, self.think = base_url.rstrip("/"), model, timeout, think

    def generate(self, system, user, max_tokens=300, temperature=0):
        data = {"model":self.model,"messages":[{"role":"system","content":system},
                {"role":"user","content":user}],"stream":False,"think":self.think,
                "options":{"temperature":temperature,"num_predict":max_tokens}}
        response = post_json(f"{self.base_url}/api/chat",data,timeout=self.timeout)
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

    def choose(self, goal, state, actions):
        state_payload = {"original_goal":goal, "current_state":state}
        criteria = {name:ACTION_DESCRIPTIONS.get(name,name) for name in actions}
        payload = {"state":json.dumps(state_payload,ensure_ascii=False),
                   "questions":{"next_action":{"type":"choice",
                       "instructions":"Choose exactly one NEXT action that advances the user's original goal using current browser evidence. Do not finish before all success criteria have supporting observations. Prefer read_page if browser state is unknown. Avoid repeating ineffective actions.",
                       "criteria":criteria}}}
        result = post_json(self.url,payload,timeout=self.timeout)
        answer = result.get("answers",{}).get("next_action",{})
        action = answer.get("choice")
        probs = answer.get("probabilities") or {}
        if not action and probs:
            action = max(probs,key=probs.get)
        if action not in actions:
            raise ModelError(f"Unexpected Strands answer: {str(result)[:500]}")
        return {"action":action,"confidence":answer.get("confidence"),
                "probabilities":probs,"model":result.get("model"),"raw":result}
