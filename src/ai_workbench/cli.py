"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app
from .config import AISettings, SettingsStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="pipkinpad")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start", help="Start PipkinPad in the current directory")
    start.add_argument("--port", type=int, default=8765)
    start.add_argument("--host", default="127.0.0.1", help="Bind address; use 0.0.0.0 only on a trusted network")
    config = subparsers.add_parser("config", help="View or update AI provider settings")
    config.add_argument("--base-url"); config.add_argument("--model"); config.add_argument("--api-key")
    subparsers.add_parser("clear-config", help="Remove saved provider settings")
    args = parser.parse_args()
    store = SettingsStore()
    if args.command == "clear-config":
        store.clear(); print("Saved configuration cleared."); return
    if args.command == "config":
        old = store.load()
        if any((args.base_url, args.model, args.api_key)):
            store.save(AISettings(args.base_url or old.base_url, args.model or old.model, api_key=args.api_key or old.api_key, temperature=old.temperature))
        public = store.load().public()
        print(f"Base URL: {public['base_url']}\nModel: {public['model']}\nAPI key configured: {public['api_key_configured']}")
        return
    url = f"http://127.0.0.1:{args.port}"
    if os.environ.get("AI_WORKBENCH_NO_BROWSER") != "1":
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    
    # Use uvicorn Config and Server for proper signal handling
    config = uvicorn.Config(create_app(Path.cwd()), host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    
    def signal_handler(sig, frame):
        print("\n收到中断信号，正在关闭服务器...")
        server.should_exit = True
        # Force exit if graceful shutdown takes too long
        def force_exit():
            import time
            time.sleep(5)
            print("强制退出...")
            import os
            os._exit(1)
        threading.Thread(target=force_exit, daemon=True).start()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    server.run()


if __name__ == "__main__":
    main()
