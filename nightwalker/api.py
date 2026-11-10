import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nightwalker.a2a import A2AMessage
from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.contracts import AgentTask, BrowserExtractRequest
from nightwalker.ollama_client import OllamaUnavailable
from nightwalker.orchestrator import Orchestrator
from nightwalker.task_manager import TaskManager

logger = logging.getLogger(__name__)

browser = BrowserRuntime()
orchestrator = Orchestrator(browser)
task_manager = TaskManager(orchestrator)
STATIC_DIR = Path(__file__).parent / "static"
UI_DIR = STATIC_DIR / "ui"
UI_FILE = UI_DIR / "index.html" if (UI_DIR / "index.html").is_file() else STATIC_DIR / "index.html"


class SteelMCPSmokeRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class NaturalLanguageTaskRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=2000)
    task_id: str = Field(default_factory=lambda: "nw-" + str(uuid4()))


class TaskClarificationRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class TaskFollowUpRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await browser.start()
    except Exception as exc:
        logger.critical("Browser runtime failed to start: %s", exc, exc_info=True)
        raise
    try:
        await task_manager.restore_and_resume()
    except Exception:
        logger.exception("Task state restore failed (continuing with empty state)")
    try:
        yield
    finally:
        await task_manager.shutdown()
        await browser.close()


app = FastAPI(
    title="Ginger Night Walker",
    version="0.4.0",
    description="Local-first, least-privilege Playwright agent with remote Ollama planning",
    lifespan=lifespan,
)


if (UI_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="ui-assets")


@app.get("/", include_in_schema=False)
async def control_centre():
    return FileResponse(UI_FILE)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ginger-night-walker"}


@app.get("/health/steel-mcp")
async def steel_mcp_health():
    return browser.steel_mcp_status()


@app.post("/tools/steel-mcp/smoke-test")
async def steel_mcp_smoke_test(request: SteelMCPSmokeRequest):
    from urllib.parse import urlsplit

    parsed = urlsplit(request.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="A public HTTP(S) URL is required")
    if not browser.steel_mcp.connected:
        raise HTTPException(status_code=503, detail="Steel MCP is not enabled or connected")
    try:
        browser.policy.validate_url(request.url)
        return await browser.steel_mcp.smoke_test(request.url)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:1000]) from exc


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
    task["browser"] = browser.task_session(task_id)
    return task


@app.post("/tasks", status_code=202)
async def submit_task(task: NaturalLanguageTaskRequest):
    try:
        return task_manager.submit_natural_language(task.task_id, task.objective.strip())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/clarify")
async def clarify_task(task_id: str, request: TaskClarificationRequest):
    if not request.answer.strip():
        raise HTTPException(status_code=422, detail="A clarification answer is required")
    try:
        task = task_manager.clarify(task_id, request.answer.strip())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task



@app.get("/tasks/{task_id}/trace")
async def get_task_trace(task_id: str, limit: int = 100):
    try:
        events = task_manager.trace_events(task_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    return {"task_id": task_id, "events": events}


@app.get("/tasks/{task_id}/a2a")
async def get_a2a_messages(task_id: str):
    task = task_manager.snapshot(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "messages": task.get("a2a_messages", [])}


@app.post("/tasks/{task_id}/a2a")
async def post_a2a_message(task_id: str, message: A2AMessage):
    try:
        result = task_manager.send_a2a_message(task_id, message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@app.post("/tasks/{task_id}/follow-up")
async def follow_up_task(task_id: str, request: TaskFollowUpRequest):
    if not request.message.strip():
        raise HTTPException(status_code=422, detail="A follow-up instruction is required")
    try:
        task = task_manager.add_follow_up(task_id, request.message.strip())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    try:
        task = task_manager.retry(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/tasks/{task_id}/restart", status_code=202)
async def restart_task(task_id: str):
    task = task_manager.snapshot(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") not in {"failed", "blocked", "stopped", "completed"}:
        raise HTTPException(status_code=409, detail="Only terminal tasks can be restarted")
    new_task_id = "nw-" + str(uuid4())
    try:
        return task_manager.submit_natural_language(
            new_task_id, str(task.get("objective") or "").strip()
        )
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
    if result.status in {"failed", "blocked"}:
        raise HTTPException(status_code=502, detail=result.model_dump())
    return result
