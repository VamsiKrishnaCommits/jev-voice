"""Keep internal handoff frames out of the user's speech transcript."""
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIObserverParams, RTVIProcessor
from .agent import ApprovedTranscript


class JevRTVIObserver(RTVIObserver):
    async def _handle_user_transcriptions(self, frame):
        if not isinstance(frame, ApprovedTranscript):
            await super()._handle_user_transcriptions(frame)


class JevRTVIProcessor(RTVIProcessor):
    def create_rtvi_observer(self, *, params=None, **kwargs):
        params = params or RTVIObserverParams()
        # Raw STT is the single source for transcript UI. Aggregated LLM input
        # belongs in the separate Jev handoff trace, not another speech bubble.
        params.user_llm_enabled = False
        return JevRTVIObserver(self, params=params, **kwargs)
