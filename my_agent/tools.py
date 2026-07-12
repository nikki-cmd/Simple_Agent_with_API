import json
import os
import math
import ast
import signal
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from duckduckgo_search import DDGS

class WebSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Search query")
    max_results: int = Field(default=3, ge=1, le=10, description="Max number of results")


class ReadFileArgs(BaseModel):
    path: str = Field(..., min_length=1, description="Path to the file")
    
    @field_validator("path")
    @classmethod
    def prevent_path_traversal(cls, v: str) -> str:
        resolved = Path(v).resolve()
        allowed_root = Path.cwd().resolve()
        if not str(resolved).startswith(str(allowed_root)):
            raise ValueError(f"Access outside the working directory is forbidden: {v}")
        return str(resolved)
    
class ExecuteCodeArgs(BaseModel):
    code: str = Field(..., min_length=1, max_length=5000, description="Python code to execute")
    timeout_seconds: int = Field(default=5, ge=1, le=30, description="Timeout in seconds")
    
class SendEmailArgs(BaseModel):
    to: str = Field(..., description="Recipient email")
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=5000)

    @field_validator("to")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError(f"Invalid email: {v}")
        return v.lower()

class CalculatorArgs(BaseModel):
    expression: str = Field(
        ..., 
        min_length=1, 
        max_length=500,
        description="Mathematical expression (e.g., '2**10 + math.sqrt(144)')"
    )

class GetCurrentTimeArgs(BaseModel):
    timezone_offset: int = Field(default=3, ge=-12, le=14, description="UTC offset (Moscow = +3)")

def tool_web_search(args: dict) -> str:
    validated = WebSearchArgs(**args)
    
    with DDGS() as ddgs:
        results = list(ddgs.text(validated.query, max_results=validated.max_results))
    
    if not results:
        return "No results found."
    
    output = []
    for i, r in enumerate(results, 1):
        output.append(f"{i}. {r.get('title', 'No title')}\n"
                      f"   URL: {r.get('href', 'N/A')}\n"
                      f"   {r.get('body', 'No description')}")
    return "\n\n".join(output)

def tool_read_file(args: dict) -> str:
    validated = ReadFileArgs(**args)
    path = Path(validated.path)
    
    if not path.exists():
        raise FileNotFoundError(f"File not found: {validated.path}")
    if not path.is_file():
        raise ValueError(f"This is not a file: {validated.path}")
    if path.stat().st_size > 1_000_000:  # 1 MB limit
        raise ValueError(f"File is too large ({path.stat().st_size} bytes). Maximum 1 MB.")
    
    return path.read_text(encoding="utf-8", errors="replace")

class CodeExecutionTimeout(Exception):
    pass

def _timeout_handler(signum, frame):
    raise CodeExecutionTimeout("Code execution timeout exceeded")


def tool_execute_code(args: dict) -> str:
    validated = ExecuteCodeArgs(**args)

    BLOCKED = {
        "__import__", "open", "exec", "eval", "compile",
        "getattr", "setattr", "delattr", "globals", "locals",
        "vars", "dir", "input", "breakpoint"
    }

    safe_globals = {
        "__builtins__": {
            k: v for k, v in __builtins__.items() 
            if k not in BLOCKED
        } if isinstance(__builtins__, dict) else {
            k: getattr(__builtins__, k) for k in dir(__builtins__) 
            if k not in BLOCKED and not k.startswith("_")
        },
        "math": math,
        "len": len, "range": range, "enumerate": enumerate,
        "zip": zip, "map": map, "filter": filter,
        "sorted": sorted, "reversed": reversed,
        "int": int, "float": float, "str": str, "bool": bool,
        "list": list, "dict": dict, "tuple": tuple, "set": set,
        "print": print, "abs": abs, "round": round, "sum": sum,
        "min": min, "max": max, "any": any, "all": all,
        "isinstance": isinstance, "type": type,
    }
    
    # Capture stdout
    import io
    import contextlib
    
    stdout_capture = io.StringIO()
    
    try:
        old_handler = None
        if hasattr(signal, 'SIGALRM'):
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(validated.timeout_seconds)
        
        with contextlib.redirect_stdout(stdout_capture):
            try:
                tree = ast.parse(validated.code, mode="eval")
                result = eval(compile(tree, "<sandbox>", "eval"), safe_globals)
                output = stdout_capture.getvalue()
                return f"{output}Result: {result}" if output else f"Result: {result}"
            except SyntaxError:
                # If not an expression — execute as statements
                tree = ast.parse(validated.code, mode="exec")
                exec(compile(tree, "<sandbox>", "exec"), safe_globals)
                output = stdout_capture.getvalue()
                return output if output else "Code executed successfully (no output)."
    
    except CodeExecutionTimeout:
        return f"ERROR: Timeout exceeded ({validated.timeout_seconds}s)."
    except Exception as e:
        return f"ERROR during code execution: {type(e).__name__}: {e}"
    finally:
        if hasattr(signal, 'SIGALRM') and old_handler is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

def tool_send_email(args: dict) -> str:
    """
    Stub
    """
    validated = SendEmailArgs(**args)

    log_entry = (
        f"[EMAIL SIMULATION]\n"
        f"  To:      {validated.to}\n"
        f"  Subject: {validated.subject}\n"
        f"  Body:    {validated.body[:100]}{'...' if len(validated.body) > 100 else ''}\n"
        f"  Status:  ✅ Delivered (simulation)\n"
        f"  Time:    {datetime.now(timezone.utc).isoformat()}"
    )
    return log_entry

def tool_calculator(args: dict) -> str:
    validated = CalculatorArgs(**args)
    
    allowed_chars = set("0123456789+-*/().%eE ")
    allowed_names = {"math", "sqrt", "sin", "cos", "tan", "pi", "log", "log10", "abs", "pow"}
    
    try:
        tree = ast.parse(validated.expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid mathematical expression: {e}")
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in allowed_names and not isinstance(node.ctx, ast.Load):
                raise ValueError(f"Disallowed name: {node.id}")
        elif isinstance(node, (ast.Constant, ast.BinOp, ast.UnaryOp, ast.Call, 
                                ast.Attribute, ast.Load, ast.Expression)):
            continue
        else:
            raise ValueError(f"Disallowed construct: {type(node).__name__}")
    
    safe_ns = {"math": math, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
               "tan": math.tan, "pi": math.pi, "log": math.log, "log10": math.log10,
               "abs": abs, "pow": pow}
    
    result = eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, safe_ns)
    return str(result)

def tool_get_current_time(args: dict) -> str:
    validated = GetCurrentTimeArgs(**args)
    from datetime import timedelta
    tz = timezone(timedelta(hours=validated.timezone_offset))
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")

TOOL_REGISTRY: dict[str, dict] = {
    "web_search": {
        "function": tool_web_search,
        "schema": {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Searches for information on the internet via DuckDuckGo. "
                    "Use when you need up-to-date data, facts, or news. "
                    "DO NOT use for mathematical calculations."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "default": 3, "description": "Number of results (1-10)"}
                    },
                    "required": ["query"],
                    "additionalProperties": False
                }
            }
        }
    },
    "read_file": {
        "function": tool_read_file,
        "schema": {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Reads the contents of a file at the specified path. "
                    "The path must be inside the working directory. "
                    "Maximum file size: 1 MB."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to the file"}
                    },
                    "required": ["path"],
                    "additionalProperties": False
                }
            }
        }
    },
    "execute_code": {
        "function": tool_execute_code,
        "schema": {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": (
                    "Executes Python code in an isolated sandbox and returns the result. "
                    "Available: math, print, basic types. "
                    "DO NOT use for reading/writing files or network requests."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute"},
                        "timeout_seconds": {"type": "integer", "default": 5}
                    },
                    "required": ["code"],
                    "additionalProperties": False
                }
            }
        }
    },
    "send_email": {
        "function": tool_send_email,
        "schema": {
            "type": "function",
            "function": {
                "name": "send_email",
                "description": (
                    "Sends an email message (simulation). "
                    "Use only when the user explicitly asks to send an email."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email"},
                        "subject": {"type": "string", "description": "Email subject"},
                        "body": {"type": "string", "description": "Email body"}
                    },
                    "required": ["to", "subject", "body"],
                    "additionalProperties": False
                }
            }
        }
    },
    "calculator": {
        "function": tool_calculator,
        "schema": {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": (
                    "Evaluates a mathematical expression. Supports: +, -, *, /, **, %, "
                    "math.sqrt, math.sin, math.cos, math.pi. "
                    "Use INSTEAD OF execute_code for any calculations."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "Math expression"}
                    },
                    "required": ["expression"],
                    "additionalProperties": False
                }
            }
        }
    },
    "get_current_time": {
        "function": tool_get_current_time,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "Returns the current date and time taking the timezone into account.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timezone_offset": {
                            "type": "integer", "default": 3,
                            "description": "Offset from UTC. Moscow=3, Novosibirsk=7"
                        }
                    },
                    "required": [],
                    "additionalProperties": False
                }
            }
        }
    },
}

def get_all_tool_schemas() -> list[dict]:
    """Returns a list of JSON schemas to pass to the API."""
    return [entry["schema"] for entry in TOOL_REGISTRY.values()]

def execute_tool(name: str, arguments: dict | str) -> str:
    if name not in TOOL_REGISTRY:
        return json.dumps({
            "error": "UnknownTool",
            "message": f"Tool '{name}' does not exist. Available: {list(TOOL_REGISTRY.keys())}"
        })

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as e:
            return json.dumps({
                "error": "InvalidJSON",
                "message": f"Failed to parse arguments: {e}"
            })
    
    if not isinstance(arguments, dict):
        arguments = {}
    
    try:
        func = TOOL_REGISTRY[name]["function"]
        result = func(arguments)
        return result
    except Exception as e:

        return json.dumps({
            "error": type(e).__name__,
            "message": str(e),
            "hint": "Check the parameters and try again, or ask the user a clarifying question."
        })