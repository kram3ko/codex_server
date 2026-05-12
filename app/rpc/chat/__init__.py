"""ChatService RPC package.

- service.ChatRPC     — тонкі Connect-RPC handler'и
- stream.stream_turn  — codex events → protobuf pipeline (idle, errors, persist)
- mappers             — ChatEvent ↔ pb + redaction + usage
- uploads             — resolve upload_ids у codex-input
- tts                 — async post-turn TTS attach
- guards              — pagination + ownership check
"""

from app.rpc.chat.service import ChatRPC

__all__ = ["ChatRPC"]
