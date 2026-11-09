from unittest.mock import AsyncMock
import pytest
from pipecat.frames.frames import TranscriptionFrame, InterimTranscriptionFrame
from jev_pipecat.agent import ApprovedTranscript
from jev_pipecat.observer import JevRTVIProcessor


@pytest.mark.asyncio
async def test_ui_gets_stt_but_not_internal_handoff():
    observer = JevRTVIProcessor().create_rtvi_observer()
    observer.send_rtvi_message = AsyncMock()
    await observer._handle_user_transcriptions(InterimTranscriptionFrame("hello", "u", ""))
    await observer._handle_user_transcriptions(TranscriptionFrame("hello", "u", ""))
    await observer._handle_user_transcriptions(ApprovedTranscript("hello", "user", ""))
    assert observer.send_rtvi_message.await_count == 2
    assert not observer._params.user_llm_enabled
