"""OpenAI-compatible chat client with streaming and tool calling support."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import AsyncGenerator

import httpx

from .config import AISettings

COMMAND_BLOCK = re.compile(r"```(?:bash|shell|powershell|cmd)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class AIReply:
    content: str
    commands: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""


# Tool definition for the AI's own reviewed Bash environment.  This intentionally
# has no relationship to the user's interactive browser terminal.
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "run_bash_command",
        "description": "在工作区的独立 Bash 进程中运行一条命令。每次运行都需要用户在页面审核批准。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要在独立 Bash 中运行的命令"
                }
            },
            "required": ["command"]
        }
    }
}


@dataclass
class StreamChunk:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finished: bool = False


class AIClient:
    def __init__(self, settings: AISettings):
        self.settings = settings

    def _build_url(self) -> str:
        base = self.settings.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _build_payload(self, messages: list[dict], available_tools: list[dict] | None = None, terminal_sessions: list[dict] | None = None, stream: bool = False) -> dict:
        system_content = (
            "你是一个本地开发助手。你可以使用 run_bash_command 工具在工作区的独立 Bash 环境中执行命令。"
            "当你需要执行命令时，使用工具调用而不是代码块。"
            "每次命令执行都需要用户确认。一次只请求一条命令；拿到工具输出后再决定下一步。"
            "用户的交互式 Terminal 与该工具完全分离：如果用户附加了终端内容，你可以阅读它，但绝不能向其中写入。"
            "Never claim to execute commands directly - always use the tool."
        )
        
        payload = {"model": self.settings.model, "messages": [{"role": "system", "content": system_content}, *messages], "stream": stream}
        if self.settings.temperature is not None:
            payload["temperature"] = self.settings.temperature
        if available_tools:
            payload["tools"] = available_tools
            payload["tool_choice"] = "auto"
        return payload

    async def chat(self, messages: list[dict], available_tools: list[dict] | None = None, terminal_sessions: list[dict] | None = None) -> AIReply:
        if not self.settings.api_key:
            raise ValueError("Configure an API key before using AI")
        
        payload = self._build_payload(messages, available_tools, terminal_sessions, stream=False)
        url = self._build_url()
        
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, json=payload, headers={"Authorization": f"Bearer {self.settings.api_key}"})
            response.raise_for_status()
        
        try:
            result = response.json()
            message = result["choices"][0]["message"]
            content = message.get("content") or ""
            raw_tool_calls = message.get("tool_calls", [])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("Provider returned an invalid chat response") from error
        
        tool_calls = []
        for tc in raw_tool_calls:
            try:
                func = tc.get("function", {})
                args = json.loads(func.get("arguments", "{}"))
                tool_calls.append(ToolCall(id=tc.get("id", ""), name=func.get("name", ""), arguments=args))
            except (json.JSONDecodeError, TypeError):
                continue
        
        commands = [item.strip() for item in COMMAND_BLOCK.findall(content) if item.strip()]
        return AIReply(content=content, commands=commands, tool_calls=tool_calls)

    async def chat_stream(self, messages: list[dict], available_tools: list[dict] | None = None, terminal_sessions: list[dict] | None = None) -> AsyncGenerator[StreamChunk, None]:
        """Stream chat response with support for reasoning content."""
        if not self.settings.api_key:
            raise ValueError("Configure an API key before using AI")
        
        payload = self._build_payload(messages, available_tools, terminal_sessions, stream=True)
        url = self._build_url()
        
        # Accumulate streaming tool call arguments
        tool_call_accumulator: dict[int, dict] = {}
        
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=payload, headers={"Authorization": f"Bearer {self.settings.api_key}"}) as response:
                response.raise_for_status()
                
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        yield StreamChunk(finished=True)
                        return
                    
                    try:
                        chunk = json.loads(data)
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        
                        content_piece = delta.get("content", "") or ""
                        reasoning_piece = delta.get("reasoning_content", "") or ""
                        raw_tool_calls = delta.get("tool_calls", [])
                        
                        tool_calls = []
                        for tc in raw_tool_calls:
                            try:
                                idx = tc.get("index", 0)
                                func = tc.get("function", {})
                                
                                if idx not in tool_call_accumulator:
                                    tool_call_accumulator[idx] = {"id": "", "name": "", "arguments": ""}

                                # OpenAI-compatible providers may provide id/name
                                # in a different delta from the first arguments.
                                if tc.get("id"):
                                    tool_call_accumulator[idx]["id"] = tc["id"]
                                if func.get("name"):
                                    tool_call_accumulator[idx]["name"] = func["name"]
                                
                                # Accumulate arguments piece by piece
                                args_piece = func.get("arguments", "") or ""
                                tool_call_accumulator[idx]["arguments"] += args_piece
                                
                                # Try to parse accumulated arguments
                                args_str = tool_call_accumulator[idx]["arguments"]
                                args = json.loads(args_str) if args_str else {}
                                tool_calls.append(ToolCall(
                                    id=tool_call_accumulator[idx]["id"],
                                    name=tool_call_accumulator[idx]["name"],
                                    arguments=args
                                ))
                            except (json.JSONDecodeError, TypeError):
                                # Arguments not complete yet, skip this chunk
                                continue
                        
                        yield StreamChunk(content=content_piece, reasoning=reasoning_piece, tool_calls=tool_calls)
                    except json.JSONDecodeError:
                        continue
                
                yield StreamChunk(finished=True)
