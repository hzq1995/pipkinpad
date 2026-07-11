"""FastAPI application factory for the local workbench."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from .ai import AIClient, BASH_TOOL, COMMAND_BLOCK
from .auth import COOKIE_MAX_AGE, COOKIE_NAME, issue_cookie, password_configured, verify_cookie, verify_password
from .audit import AuditLog
from .config import AISettings, SettingsStore
from .files import FileService
from .security import Workspace, WorkspaceSecurityError, new_session_token
from .terminal import TerminalSession

MAX_BASH_OUTPUT_CHARS = 100_000
MAX_UI_STATE_BYTES = 2_000_000


def create_app(root: Path, settings_store: SettingsStore | None = None) -> FastAPI:
    workspace = Workspace(root)
    files, audit = FileService(workspace), AuditLog(workspace.root)
    ui_state_path = workspace.root / ".pipkinpad-ui-state.json"
    terminals: dict[str, TerminalSession] = {}
    settings_store = settings_store or SettingsStore()
    token = new_session_token()
    app = FastAPI(title="PipkinPad", docs_url=None, redoc_url=None)
    app.state.session_token = token

    def browser_authorized(cookie: str | None) -> bool:
        return verify_cookie(settings_store.load(), cookie)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.url.path != "/api/login":
            if not browser_authorized(request.cookies.get(COOKIE_NAME)):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return await call_next(request)

    def authorized(value: str | None) -> None:
        if value != token:
            raise HTTPException(403, "Invalid local session")

    def load_ui_state() -> dict:
        try:
            state = json.loads(ui_state_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_ui_state(state: dict) -> None:
        encoded = json.dumps(state, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_UI_STATE_BYTES:
            raise HTTPException(413, "UI state is too large")
        temporary = ui_state_path.with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(ui_state_path)

    @app.exception_handler(WorkspaceSecurityError)
    async def workspace_error(_, __):
        return HTMLResponse("Workspace boundary violation", status_code=403)

    @app.get("/")
    async def index(request: Request):
        static = Path(__file__).parent / "static"
        page = "index.html" if browser_authorized(request.cookies.get(COOKIE_NAME)) else "login.html"
        return HTMLResponse((static / page).read_text(encoding="utf-8"))

    @app.post("/api/login")
    async def login(request: Request):
        settings = settings_store.load()
        if not password_configured(settings):
            return {"ok": True}
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid request")
        password = body.get("password") if isinstance(body, dict) else None
        if not isinstance(password, str) or not verify_password(settings, password):
            audit.record("auth.login_failed", client=request.client.host if request.client else "unknown")
            raise HTTPException(401, "Invalid password")
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE_NAME, issue_cookie(settings), max_age=COOKIE_MAX_AGE,
                            httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
        audit.record("auth.login")
        return response

    @app.get("/.well-known/appspecific/com.chrome.devtools.json")
    async def chrome_devtools():
        return {}

    @app.get("/api/bootstrap")
    async def bootstrap():
        return {"token": token, "root": workspace.root.name}

    @app.get("/api/ui-state")
    async def get_ui_state(x_session_token: str | None = Header(None)):
        authorized(x_session_token)
        return {"state": load_ui_state()}

    @app.put("/api/ui-state")
    async def set_ui_state(body: dict, x_session_token: str | None = Header(None)):
        authorized(x_session_token)
        state = body.get("state")
        if not isinstance(state, dict):
            raise HTTPException(422, "state must be an object")
        save_ui_state(state)
        return {"ok": True}

    @app.get("/api/files")
    async def list_files(path: str = "", x_session_token: str | None = Header(None)):
        authorized(x_session_token)
        return files.list_tree(path)

    @app.get("/api/files/content")
    async def file_content(path: str, x_session_token: str | None = Header(None)):
        authorized(x_session_token)
        try:
            return {"content": files.read_text(path)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.put("/api/files/content")
    async def save_content(path: str, content: str, x_session_token: str | None = Header(None)):
        authorized(x_session_token); files.write_text(path, content); audit.record("file.write", path=path); return {"ok": True}

    @app.post("/api/files/directory")
    async def create_directory(path: str, x_session_token: str | None = Header(None)):
        authorized(x_session_token); files.mkdir(path); audit.record("file.mkdir", path=path); return {"ok": True}

    @app.post("/api/files/move")
    async def move_file(source: str, destination: str, x_session_token: str | None = Header(None)):
        authorized(x_session_token); files.move(source, destination); audit.record("file.move", source=source, destination=destination); return {"ok": True}

    @app.delete("/api/files")
    async def delete_file(path: str, x_session_token: str | None = Header(None)):
        authorized(x_session_token); files.delete(path); audit.record("file.delete", path=path); return {"ok": True}

    @app.post("/api/files/upload")
    async def upload(path: str = Form(""), upload: UploadFile = File(...), x_session_token: str | None = Header(None)):
        authorized(x_session_token)
        target = workspace.resolve(str(Path(path) / (upload.filename or "upload")))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await upload.read())
        audit.record("file.upload", path=workspace.relative(target)); return {"ok": True}

    @app.get("/api/files/download")
    async def download(path: str, x_session_token: str | None = Header(None)):
        authorized(x_session_token); target = workspace.resolve(path)
        if not target.is_file(): raise HTTPException(404, "File not found")
        return FileResponse(target, filename=target.name)

    @app.get("/api/settings")
    async def get_settings(x_session_token: str | None = Header(None)):
        authorized(x_session_token); return settings_store.load().public()

    @app.put("/api/settings")
    async def set_settings(body: dict, x_session_token: str | None = Header(None)):
        authorized(x_session_token)
        old = settings_store.load()
        settings = AISettings(base_url=body.get("base_url", old.base_url), model=body.get("model", old.model),
                              api_key=body.get("api_key") or old.api_key, temperature=body.get("temperature", old.temperature))
        settings_store.save(settings); audit.record("settings.updated"); return settings.public()

    @app.post("/api/ai/chat")
    async def chat(body: dict, x_session_token: str | None = Header(None)):
        authorized(x_session_token)
        messages = body.get("messages", [])
        if not isinstance(messages, list): raise HTTPException(422, "messages must be a list")
        
        client = AIClient(settings_store.load())
        
        async def generate():
            full_content = ""
            all_tool_calls = []
            try:
                async for chunk in client.chat_stream(messages, [BASH_TOOL]):
                    if chunk.finished:
                        commands = [item.strip() for item in COMMAND_BLOCK.findall(full_content) if item.strip()]
                        final = json.dumps({
                            "finished": True,
                            "commands": commands,
                            "tool_calls": [call for call in all_tool_calls if call["command"]],
                        }, ensure_ascii=False)
                        yield f"data: {final}\n\n"
                        yield "data: [DONE]\n\n"
                        break

                    full_content += chunk.content
                    for tc in chunk.tool_calls:
                        if tc.name != "run_bash_command":
                            continue
                        existing = next((call for call in all_tool_calls if call["id"] == tc.id), None)
                        if existing:
                            existing["command"] = tc.arguments.get("command", existing["command"])
                        else:
                            all_tool_calls.append({
                                "id": tc.id,
                                "name": tc.name,
                                "command": tc.arguments.get("command", ""),
                            })

                    tool_calls_data = [{
                        "id": tc.id,
                        "name": tc.name,
                        "command": tc.arguments.get("command", ""),
                    } for tc in chunk.tool_calls]
                    data = json.dumps({
                        "content": chunk.content,
                        "reasoning": chunk.reasoning,
                        "tool_calls": tool_calls_data,
                    }, ensure_ascii=False)
                    yield f"data: {data}\n\n"
            except Exception as error:
                audit.record("ai.chat_error", error=str(error)[:500])
                failure = json.dumps({"finished": True, "error": str(error)}, ensure_ascii=False)
                yield f"data: {failure}\n\n"
                yield "data: [DONE]\n\n"
        
        audit.record("ai.chat", context_items=len(body.get("attachments", [])))
        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/api/ai/execute-command")
    async def execute_command(body: dict, x_session_token: str | None = Header(None)):
        authorized(x_session_token)
        command = body.get("command", "")
        if not isinstance(command, str) or not command.strip():
            raise HTTPException(422, "command is required")
        
        try:
            bash = shutil.which("bash")
            if not bash:
                raise RuntimeError("未找到 Bash；请安装 Bash 后重试")
            result = await asyncio.to_thread(
                subprocess.run, [bash, "-lc", command], cwd=str(workspace.root),
                # Do not use text=True here: on Windows it uses the active GBK
                # code page, while Bash commonly writes UTF-8. A decode error in
                # subprocess' reader thread otherwise discards command output.
                capture_output=True, timeout=30,
            )
            output = (result.stdout or b"").decode("utf-8", errors="replace")
            output += (result.stderr or b"").decode("utf-8", errors="replace")
            if len(output) > MAX_BASH_OUTPUT_CHARS:
                output = output[:MAX_BASH_OUTPUT_CHARS] + "\n…（输出已截断）"
            audit.record("ai.bash_executed", command=command[:200], returncode=result.returncode)
            return {"output": output, "returncode": result.returncode}
        except subprocess.TimeoutExpired:
            audit.record("ai.bash_timeout", command=command[:200])
            return {"output": "命令执行超时（30 秒）", "returncode": -1}
        except Exception as e:
            audit.record("ai.bash_error", error=str(e)[:200])
            return {"output": f"错误: {str(e)}", "returncode": -1}

    @app.websocket("/ws/terminal")
    async def terminal_socket(socket: WebSocket):
        if not browser_authorized(socket.cookies.get(COOKIE_NAME)) or socket.query_params.get("token") != token:
            await socket.close(code=1008); return
        session_id = socket.query_params.get("session", "default")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
            await socket.close(code=1008); return
        await socket.accept()
        terminal = terminals.setdefault(session_id, TerminalSession(workspace.root))
        async def sender():
            async for chunk in terminal.read_chunks(): await socket.send_text(chunk)
        task = asyncio.create_task(sender())
        try:
            while True:
                message = await socket.receive_json()
                if message.get("type") == "input":
                    data = message.get("data", "")
                    await terminal.write(data); audit.record("terminal.input", length=len(data))
                elif message.get("type") == "resize":
                    await terminal.resize(int(message.get("cols", 120)), int(message.get("rows", 30)))
        except WebSocketDisconnect:
            pass
        finally:
            task.cancel()

    @app.on_event("shutdown")
    async def shutdown_terminals():
        """Close all PTY sessions on server shutdown."""
        for terminal in terminals.values():
            await terminal.close()
        terminals.clear()

    return app
