"""account_manager - 多平台账号管理后台"""
import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from core.db import init_db
from core.registry import load_all
from api.accounts import router as accounts_router
from api.tasks import router as tasks_router
from api.platforms import router as platforms_router
from api.proxies import router as proxies_router
from api.config import router as config_router
from api.actions import router as actions_router
from api.integrations import router as integrations_router
from api.chatgpt import router as chatgpt_router

EXPECTED_RUNTIME = os.getenv("APP_RUNTIME_HINT", "uv run")
IS_FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_VENV_PATH = os.path.join(PROJECT_ROOT, ".venv")


def _detect_virtual_env() -> str:
    virtual_env = os.getenv("VIRTUAL_ENV")
    if virtual_env:
        return os.path.basename(virtual_env)

    if sys.prefix != sys.base_prefix:
        return os.path.basename(sys.prefix)
    return ""


def _is_expected_runtime() -> bool:
    prefix = os.path.normpath(sys.prefix)
    expected_prefix = os.path.normpath(PROJECT_VENV_PATH)
    return prefix == expected_prefix


def _print_runtime_info() -> None:
    current_env = _detect_virtual_env()
    print(f"[Runtime] Python: {sys.executable}")
    print(f"[Runtime] Virtual Env: {current_env or '未检测到'}")
    if IS_FROZEN:
        print("[Runtime] Packaged backend detected")
        return
    if not current_env:
        print(
            f"[WARN] 未检测到虚拟环境，推荐通过 '{EXPECTED_RUNTIME} python main.py' 启动，"
            "否则 Turnstile Solver 可能因依赖缺失而无法启动。"
        )
        return
    if not _is_expected_runtime():
        print(
            f"[WARN] 当前虚拟环境为 '{current_env}'，推荐使用项目环境 '{PROJECT_VENV_PATH}' 启动，"
            "否则 Turnstile Solver 可能因依赖缺失而无法启动。"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _print_runtime_info()
    init_db()
    load_all()
    print("[OK] 数据库初始化完成")
    from core.registry import list_platforms
    print(f"[OK] 已加载平台: {[p['name'] for p in list_platforms()]}")
    from core.scheduler import scheduler
    scheduler.start()
    from services.solver_manager import start_async
    start_async()
    yield
    from core.scheduler import scheduler as _scheduler
    _scheduler.stop()
    from services.solver_manager import stop
    stop()


app = FastAPI(title="Account Manager", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(platforms_router, prefix="/api")
app.include_router(proxies_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(actions_router, prefix="/api")
app.include_router(integrations_router, prefix="/api")
app.include_router(chatgpt_router, prefix="/api")


@app.get("/api/solver/status")
def solver_status():
    from services.solver_manager import is_running
    return {"running": is_running()}


@app.post("/api/solver/restart")
def solver_restart():
    from services.solver_manager import stop, start_async
    stop()
    start_async()
    return {"message": "重启中"}


_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(_static_dir, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        return FileResponse(os.path.join(_static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = os.getenv("APP_RELOAD", "0").lower() in {"1", "true", "yes"}
    uvicorn.run("main:app", host=host, port=port, reload=reload_enabled)
