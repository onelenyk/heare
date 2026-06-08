import pytest

from src.memory.base import NoopBackend
from src.memory.tools import forget, memory_status, recall, remember


@pytest.fixture
def backend():
    return NoopBackend()


@pytest.mark.asyncio
async def test_remember_success(backend):
    result = await remember(backend, '{"type": "fact", "content": "test memory"}')
    assert result["success"] is True
    assert "memory_id" in result
    assert "spoken" in result


@pytest.mark.asyncio
async def test_remember_invalid_json(backend):
    result = await remember(backend, "not json")
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_remember_missing_content(backend):
    result = await remember(backend, '{"type": "fact"}')
    assert result["success"] is False


@pytest.mark.asyncio
async def test_remember_invalid_type(backend):
    result = await remember(backend, '{"type": "invalid", "content": "x"}')
    assert result["success"] is False


@pytest.mark.asyncio
async def test_recall_success(backend):
    result = await recall(backend, '{"query": "test"}')
    assert result["success"] is True
    assert "results" in result
    assert result["results"] == []


@pytest.mark.asyncio
async def test_recall_missing_query(backend):
    result = await recall(backend, '{"query": ""}')
    assert result["success"] is False


@pytest.mark.asyncio
async def test_forget_success(backend):
    result = await forget(backend, '{"memory_id": "abc123"}')
    assert result["success"] is True  # NoopBackend always returns True
    assert "spoken" in result


@pytest.mark.asyncio
async def test_forget_missing_id(backend):
    result = await forget(backend, '{"memory_id": ""}')
    assert result["success"] is False


@pytest.mark.asyncio
async def test_memory_status(backend):
    result = await memory_status(backend, "{}")
    assert result["success"] is True
    assert result["stats"] == {"total": 0}
    assert "spoken" in result
