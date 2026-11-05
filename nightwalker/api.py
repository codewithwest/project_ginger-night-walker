from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.contracts import AgentTask, BrowserExtractRequest
from nightwalker.ollama_client import OllamaUnavailable
from nightwalker.orchestrator import Orchestrator
from nightwalker.task_manager import TaskManager

browser = BrowserRuntime()
orchestrator = Orchestrator(browser)
task_manager = TaskManager(orchestrator)
UI_FILE = Path(__file__).parent / "static" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await browser.start()
    yield
    await browser.close()


app = FastAPI(
    title="Ginger Night Walker",
    version="0.3.0",
    description="Least-privilege, context-isolated Playwright agent runtime with remote Ollama",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
async def control_centre():
    return FileResponse(UI_FILE)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ginger-night-walker"}


@app.get("/health/ollama")
async def ollama_health():
    try:
        return await orchestrator.ollama.health()
    except OllamaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/tasks")
async def list_tasks():
    return {"tasks": task_manager.list_tasks()}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = task_manager.snapshot(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/tasks", status_code=202)
async def submit_task(task: AgentTask):
    try:
        return task_manager.submit(task)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/tasks/{task_id}")
async def stop_task(task_id: str):
    task = await task_manager.stop(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


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
