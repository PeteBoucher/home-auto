"""Unit tests for the Fire TV ADB key-send logic."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.devices import firetv


@pytest.fixture(autouse=True)
def _reset_ftv():
    firetv._ftv = None
    yield
    firetv._ftv = None


class TestSendKey:
    def test_unknown_action_returns_false(self):
        firetv._ftv = AsyncMock(available=True)
        assert asyncio.run(firetv.send_key("nonsense")) is False

    def test_no_connection_returns_false(self):
        firetv._ftv = None
        assert asyncio.run(firetv.send_key("play_pause")) is False

    def test_unavailable_connection_returns_false(self):
        firetv._ftv = AsyncMock(available=False)
        assert asyncio.run(firetv.send_key("play_pause")) is False

    def test_known_action_calls_matching_method(self):
        mock_ftv = AsyncMock(available=True)
        firetv._ftv = mock_ftv
        result = asyncio.run(firetv.send_key("volume_up"))
        assert result is True
        mock_ftv.volume_up.assert_awaited_once()

    def test_exception_is_caught_and_returns_false(self):
        mock_ftv = AsyncMock(available=True)
        mock_ftv.back.side_effect = ConnectionError("adb gone")
        firetv._ftv = mock_ftv
        assert asyncio.run(firetv.send_key("back")) is False
