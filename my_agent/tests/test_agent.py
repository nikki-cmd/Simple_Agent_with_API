"""
tests/test_agent.py — Тесты для инструментов и агентного цикла.
Запуск: pytest tests/ -v
"""

import json
import os
import tempfile
import pytest

# Импорты из нашего проекта
from tools import (
    execute_tool, 
    tool_web_search, tool_read_file, tool_execute_code,
    tool_send_email, tool_calculator, tool_get_current_time,
    WebSearchArgs, ReadFileArgs, ExecuteCodeArgs, SendEmailArgs,
    CalculatorArgs, TOOL_REGISTRY
)


# ============================================================
# A. ТЕСТЫ ВАЛИДАЦИИ (Pydantic)
# ============================================================

class TestValidation:
    """Тестируем, что Pydantic правильно отвергает плохие аргументы."""

    def test_web_search_empty_query_rejected(self):
        with pytest.raises(Exception):
            WebSearchArgs(query="")
    
    def test_web_search_too_long_query_rejected(self):
        with pytest.raises(Exception):
            WebSearchArgs(query="x" * 600)
    
    def test_read_file_path_traversal_blocked(self):
        with pytest.raises(Exception):
            ReadFileArgs(path="../../../../etc/passwd")
    
    def test_send_email_invalid_format_rejected(self):
        with pytest.raises(Exception):
            SendEmailArgs(to="not-an-email", subject="test", body="test")
    
    def test_send_email_valid(self):
        args = SendEmailArgs(to="User@Example.COM", subject="Hi", body="Hello")
        assert args.to == "user@example.com"  # lowercased
    
    def test_execute_code_timeout_bounds(self):
        with pytest.raises(Exception):
            ExecuteCodeArgs(code="1+1", timeout_seconds=100)  # max=30


# ============================================================
# B. ТЕСТЫ ИСПОЛНЕНИЯ ИНСТРУМЕНТОВ
# ============================================================

class TestToolExecution:
    """Тестируем реальное исполнение каждого инструмента."""

    # --- Calculator ---
    def test_calculator_simple(self):
        result = execute_tool("calculator", {"expression": "2 + 2"})
        assert result == "4"
    
    def test_calculator_complex(self):
        result = execute_tool("calculator", {"expression": "2**10"})
        assert result == "1024"
    
    def test_calculator_with_math(self):
        result = execute_tool("calculator", {"expression": "math.sqrt(144)"})
        assert "12" in result
    
    def test_calculator_blocks_dangerous(self):
        result = execute_tool("calculator", {"expression": "__import__('os').system('ls')"})
        assert "error" in result.lower() or "ОШИБКА" in result or "Запрещённое" in result
    
    def test_calculator_invalid_expression(self):
        result = execute_tool("calculator", {"expression": "2 + + + 2"})
        assert "error" in result.lower() or "ОШИБКА" in result or "Невалидное" in result

    # --- Execute Code ---
    def test_execute_code_simple_expression(self):
        result = execute_tool("execute_code", {"code": "len([1,2,3])"})
        assert "3" in result
    
    def test_execute_code_print(self):
        result = execute_tool("execute_code", {"code": "print('hello world')"})
        assert "hello world" in result
    
    def test_execute_code_multiline(self):
        code = "x = 10\ny = 20\nprint(x + y)"
        result = execute_tool("execute_code", {"code": code})
        assert "30" in result
    
    def test_execute_code_blocks_import(self):
        result = execute_tool("execute_code", {"code": "import os"})
        assert "ОШИБКА" in result or "error" in result.lower()
    
    def test_execute_code_handles_syntax_error(self):
        result = execute_tool("execute_code", {"code": "def broken("})
        assert "ОШИБКА" in result or "error" in result.lower()

    # --- Read File ---
    def test_read_file_success(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", 
                                          delete=False, dir=".") as f:
            f.write("test content 123")
            f.flush()
            fname = os.path.basename(f.name)
        
        try:
            result = execute_tool("read_file", {"path": fname})
            assert "test content 123" in result
        finally:
            os.unlink(fname)
    
    def test_read_file_not_found(self):
        result = execute_tool("read_file", {"path": "nonexistent_file_xyz.txt"})
        assert "error" in result.lower() or "не найден" in result.lower()
    
    def test_read_file_path_traversal(self):
        result = execute_tool("read_file", {"path": "../../../etc/passwd"})
        assert "error" in result.lower() or "запрещён" in result.lower()

    # --- Send Email (симуляция) ---
    def test_send_email_simulation(self):
        result = execute_tool("send_email", {
            "to": "test@example.com",
            "subject": "Test Subject",
            "body": "Hello from agent!"
        })
        assert "Доставлено" in result or "Delivered" in result or "SIMULATION" in result
    
    def test_send_email_invalid_address(self):
        result = execute_tool("send_email", {
            "to": "invalid",
            "subject": "Test",
            "body": "Test"
        })
        assert "error" in result.lower() or "Невалидный" in result

    # --- Get Current Time ---
    def test_get_current_time(self):
        result = execute_tool("get_current_time", {"timezone_offset": 3})
        assert "20" in result  # содержит год 20xx
    
    def test_get_current_time_default(self):
        result = execute_tool("get_current_time", {})
        assert len(result) > 5  # вернул что-то осмысленное


# ============================================================
# C. ТЕСТЫ ДИСПЕТЧЕРА execute_tool
# ============================================================

class TestDispatcher:
    """Тестируем универсальный execute_tool: обработка ошибок, неизвестные инструменты."""

    def test_unknown_tool_returns_error(self):
        result = execute_tool("fly_to_moon", {"fuel": 100})
        parsed = json.loads(result)
        assert parsed["error"] == "UnknownTool"
    
    def test_malformed_json_args(self):
        result = execute_tool("calculator", "this is not json {{{")
        parsed = json.loads(result)
        assert parsed["error"] == "InvalidJSON"
    
    def test_missing_required_args(self):
        # calculator требует "expression", передаём пустой dict
        result = execute_tool("calculator", {})
        assert "error" in result.lower() or "ОШИБКА" in result or "validation" in result.lower() or "required" in result.lower() or "missing" in result.lower()
    
    def test_always_returns_string(self):
        """execute_tool НИКОГДА не выбрасывает исключение — всегда строка."""
        test_cases = [
            ("nonexistent", {}),
            ("calculator", {"expression": "1/0"}),
            ("execute_code", {"code": "raise RuntimeError('boom')"}),
            ("read_file", {"path": "/no/such/file"}),
        ]
        for name, args in test_cases:
            result = execute_tool(name, args)
            assert isinstance(result, str), f"{name} вернул не строку: {type(result)}"


# ============================================================
# D. ТЕСТЫ РЕЕСТРА
# ============================================================

class TestRegistry:
    """Проверяем целостность TOOL_REGISTRY."""
    
    def test_all_tools_have_schema(self):
        for name, entry in TOOL_REGISTRY.items():
            assert "schema" in entry, f"{name}: нет schema"
            assert "function" in entry, f"{name}: нет function"
            schema = entry["schema"]
            assert schema["function"]["name"] == name, f"{name}: имя в схеме не совпадает"
    
    def test_all_schemas_have_parameters(self):
        for name, entry in TOOL_REGISTRY.items():
            params = entry["schema"]["function"]["parameters"]
            assert params["type"] == "object", f"{name}: parameters.type != object"
            assert "properties" in params, f"{name}: нет properties"


# ============================================================
# E. ИНТЕГРАЦИОННЫЙ ТЕСТ (требует запущенную Ollama)
# ============================================================

@pytest.mark.integration
class TestAgentIntegration:
    """
    Эти тесты требуют запущенную Ollama с моделью qwen2.5:7b.
    Запуск: pytest tests/ -v -m integration
    Пропуск: pytest tests/ -v -m "not integration"
    """
    
    def test_agent_calculator_query(self):
        from agent import Agent
        agent = Agent(model="qwen2.5:7b", max_iterations=5)
        response = agent.run("Сколько будет 256 умножить на 128?")
        # Модель должна вызвать calculator и вернуть 32768
        assert "32768" in response or "32 768" in response
    
    def test_agent_time_query(self):
        from agent import Agent
        agent = Agent(model="qwen2.5:7b", max_iterations=5)
        response = agent.run("Сколько сейчас времени в Москве?")
        # Должна содержать время в каком-то формате
        assert any(c.isdigit() for c in response)
    
    def test_agent_handles_error_gracefully(self):
        from agent import Agent
        agent = Agent(model="qwen2.5:7b", max_iterations=5)
        response = agent.run("Прочитай файл /etc/shadow")
        # Должна либо вернуть ошибку, либо отказаться
        assert len(response) > 0  # не упала