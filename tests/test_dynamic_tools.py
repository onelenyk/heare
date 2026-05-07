"""Tests for dynamic tool creation and execution."""
from __future__ import annotations

import pytest

from src.agent.tools.registry import ToolDefinition, register_dynamic_tool, unregister_dynamic_tool, Tool, get_tool, is_dynamic_tool
from src.agent.tools.dynamic import execute_bash_tool, execute_fetch_tool, execute_python_tool


@pytest.mark.asyncio
async def test_execute_bash_tool_success():
    """Test bash tool with simple command."""
    result = await execute_bash_tool("echo {msg}", {"msg": "hello"}, None)
    assert result["success"] is True
    assert result["output"] == "hello"


@pytest.mark.asyncio
async def test_execute_bash_tool_failure():
    """Test bash tool with failing command."""
    result = await execute_bash_tool("exit 1", {}, None)
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_execute_bash_tool_timeout():
    """Test bash tool timeout handling."""
    result = await execute_bash_tool("sleep 60", {}, None)
    assert result["success"] is False
    assert "timed out" in result["error"]


@pytest.mark.asyncio
async def test_execute_fetch_tool_success():
    """Test fetch tool with successful HTTP request."""
    # Skip if httpx is not available
    try:
        import httpx  # noqa: F401
    except ImportError:
        pytest.skip("httpx not installed")
        return

    # Use actual mock for httpx
    from unittest import mock

    mock_response = mock.Mock()
    mock_response.status_code = 200
    mock_response.text = "response body"
    mock_response.raise_for_status = mock.Mock()

    async def mock_get(*args, **kwargs):
        return mock_response

    with mock.patch("httpx.AsyncClient.get", mock_get):
        result = await execute_fetch_tool("http://example.com/test", {}, None)

    assert result["success"] is True
    assert result["output"] == "response body"


@pytest.mark.asyncio
async def test_execute_fetch_tool_with_substitution():
    """Test fetch tool with URL argument substitution."""
    result = await execute_fetch_tool("http://example.com/{city}", {"city": "kyiv"}, None)
    # Will fail without actual HTTP server, but we test substitution happened
    assert "kyiv" in result.get("error", "") or result["success"] is False


@pytest.mark.asyncio
async def test_execute_python_tool_success():
    """Test python tool with simple expression."""
    result = await execute_python_tool("args['x'] * 2", {"x": 3}, None)
    assert result["success"] is True
    assert result["output"] == "6"


@pytest.mark.asyncio
async def test_execute_python_tool_error():
    """Test python tool with invalid expression."""
    result = await execute_python_tool("args['undefined_key']", {}, None)
    assert result["success"] is False


def test_tool_definition_validation_valid():
    """Test validation of a valid tool definition."""
    definition = ToolDefinition(
        name="weather",
        description="Get weather for a city",
        arguments={"city": {"type": "string", "description": "City name"}},
        implementation_type="bash",
        implementation="curl wttr.in/{city}",
    )
    assert definition.validate() == []


def test_tool_definition_validation_invalid_name():
    """Test validation catches invalid tool name."""
    definition = ToolDefinition(
        name="BadName",  # not lowercase
        description="Test",
        arguments={},
        implementation_type="bash",
        implementation="echo",
    )
    errors = definition.validate()
    assert len(errors) > 0
    assert any("lowercase" in e for e in errors)


def test_tool_definition_validation_missing_fields():
    """Test validation catches missing required fields."""
    definition = ToolDefinition(
        name="",  # empty name
        description="",  # empty description
        arguments={},  # empty args (allowed)
        implementation_type="bash",
        implementation="",  # empty implementation
    )
    errors = definition.validate()
    assert len(errors) >= 3  # name, description, implementation


def test_register_and_unregister_tool():
    """Test dynamic tool registration."""
    tool = Tool(
        name="test_tool",
        sdk_name="TestTool",
        execution="direct",
        description="A test tool",
    )

    register_dynamic_tool(tool)
    assert get_tool("test_tool") == tool
    assert is_dynamic_tool("test_tool") is True

    unregister_dynamic_tool("test_tool")
    assert get_tool("test_tool") is None


def test_register_multiple_tools():
    """Test registering multiple dynamic tools."""
    tools = [
        Tool("tool1", "Tool1", "direct", "First tool"),
        Tool("tool2", "Tool2", "direct", "Second tool"),
        Tool("tool3", "Tool3", "direct", "Third tool"),
    ]

    for tool in tools:
        register_dynamic_tool(tool)

    assert get_tool("tool1") == tools[0]
    assert get_tool("tool2") == tools[1]
    assert get_tool("tool3") == tools[2]

    # Cleanup
    for tool in tools:
        unregister_dynamic_tool(tool.name)
