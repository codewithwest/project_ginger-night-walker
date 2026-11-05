from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.contracts import AgentTask, BrowserExtractRequest
from nightwalker.ollama_client import OllamaUnavailable
from nightwalker.orchestrator import Orchestrator

browser = BrowserRuntime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await browser.start()
    yield
    await browser.close()


app = FastAPI(
    title="Ginger Night Walker",
    version="0.2.0",
    description="Least-privilege, context-isolated Playwright agent runtime with remote Ollama",
    lifespan=lifespan,
)
orchestrator = Orchestrator(browser)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ginger-night-walker"}


@app.get("/health/ollama")
async def ollama_health():
    try:
        return await orchestrator.ollama.health()
    except OllamaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/tasks/agent")
async def run_agent(task: AgentTask):
    try:
        return await orchestrator.run_agent_task(task)
    except OllamaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/tasks/browser-extract")
async def browser_extract(request: BrowserExtractRequest):
    result = await orchestrator.run_browser_extract(request)
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.model_dump())
    return result
