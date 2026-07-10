"""Cross-platform PTY sessions for a browser terminal emulator."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
from pathlib import Path


class TerminalSession:
    """One persistent shell backed by a real pseudo-terminal, not stdio pipes."""

    def __init__(self, cwd: Path):
        self.cwd = cwd
        self._pty = None
        self._master_fd: int | None = None
        self._process: subprocess.Popen | None = None

    async def start(self) -> None:
        if self.is_alive:
            return
        if platform.system() == "Windows":
            from winpty import PtyProcess  # pywinpty is installed only on Windows

            # WinPTY must receive the parent process environment explicitly.
            # In particular, Codex/IDE launches may omit PATHEXT; PowerShell then
            # cannot resolve any extensionless executable such as `python`, `pip`,
            # `git`, or `node` even when it is on PATH.
            environment = os.environ.copy()
            environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"
            powershell = shutil.which("powershell.exe") or "powershell.exe"
            command = (
                '[Console]::InputEncoding=[Text.UTF8Encoding]::new();'
                '[Console]::OutputEncoding=[Text.UTF8Encoding]::new();'
                '$OutputEncoding=[Console]::OutputEncoding'
            )
            self._pty = PtyProcess.spawn(
                [powershell, "-NoLogo", "-NoExit", "-Command", command],
                cwd=str(self.cwd), env=environment, dimensions=(30, 120),
            )
            return

        import pty

        master, slave = pty.openpty()
        shell = os.environ.get("SHELL", "/bin/sh")
        self._process = subprocess.Popen(
            [shell, "-i"], cwd=self.cwd, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
        )
        os.close(slave)
        self._master_fd = master

    @property
    def is_alive(self) -> bool:
        if platform.system() == "Windows":
            return bool(self._pty and self._pty.isalive())
        return bool(self._process and self._process.poll() is None)

    async def write(self, data: str) -> None:
        await self.start()
        if self._pty:
            await asyncio.to_thread(self._pty.write, data)
        elif self._master_fd is not None:
            await asyncio.to_thread(os.write, self._master_fd, data.encode("utf-8"))

    async def resize(self, columns: int, rows: int) -> None:
        await self.start()
        columns, rows = max(2, min(columns, 500)), max(2, min(rows, 200))
        if self._pty:
            await asyncio.to_thread(self._pty.setwinsize, rows, columns)
        elif self._master_fd is not None:
            import fcntl
            import struct
            import termios

            await asyncio.to_thread(
                fcntl.ioctl, self._master_fd, termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, columns, 0, 0),
            )

    async def read_chunks(self):
        await self.start()
        while self.is_alive:
            try:
                if self._pty:
                    chunk = await asyncio.to_thread(self._pty.read, 4096)
                elif self._master_fd is not None:
                    chunk = await asyncio.to_thread(os.read, self._master_fd, 4096)
                    chunk = chunk.decode("utf-8", errors="replace")
                else:
                    return
            except (EOFError, OSError):
                return
            if chunk:
                yield chunk

    async def close(self) -> None:
        """Terminate the underlying PTY process."""
        if platform.system() == "Windows":
            if self._pty:
                try:
                    self._pty.close(force=True)
                except Exception:
                    pass
                self._pty = None
        else:
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=3)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
                self._process = None
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None

    async def close(self) -> None:
        if self._pty and self._pty.isalive():
            await asyncio.to_thread(self._pty.terminate)
        elif self._process and self._process.poll() is None:
            self._process.terminate()
            await asyncio.to_thread(self._process.wait)
        if self._master_fd is not None:
            os.close(self._master_fd)
            self._master_fd = None
