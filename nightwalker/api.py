from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.contracts import BrowserExtractRequest
from nightwalker.orchestrator import Orchestrator

browser = BrowserRuntime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await browser.start()
    yield
    await browser.close()


app = FastAPI(
    title="Ginger Night Walker",
    version="0.1.0",
    description="Least-privilege, context-isolated Playwright agent runtime",
    lifespan=lifespan,
)
orchestrator = Orchestrator(browser)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ginger-night-walker"}


@app.post("/tasks/browser-extract")
async def browser_extract(request: BrowserExtractRequest):
    result = await orchestrator.run_browser_extract(request)
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.model_dump())
    return result
