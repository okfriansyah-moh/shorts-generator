"""TTS synthesis — fast stub (returns None, pipeline skips narration)."""
from __future__ import annotations
from typing import Optional
from contracts.hook import HookResult
from contracts.tts import TTSResult

def process(
    hook_result: HookResult,
    config: dict,
    output_dir: str,
) -> Optional[TTSResult]:
    """Return None — pipeline continues without TTS narration."""
    return None
