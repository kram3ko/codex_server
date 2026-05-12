"""Stream-state накопичувач для одного codex turn'у.

Web (`rpc/chat/stream.py`) і TG (`tg/turn/stream.py`) обидва крутять той самий
ChatEvent-стрім і збирають однаковий стан: streamed buffer, tool_calls,
attachments, final_text. Раніше match-case з state-мутацією дублювався у двох
місцях. Тут — одне `absorb(ev)` на джерело правди; side-effects (yield pb /
progress.note) лишаються у викликачів, бо там вони різні по суті.
"""

from dataclasses import dataclass, field

from app.services.codex.events import (
    Attachment,
    ChatEvent,
    DoneEvent,
    TokenEvent,
    ToolCallEvent,
    ToolCallRecord,
    ToolResultEvent,
)


@dataclass(slots=True)
class StreamCollector:
    buffer: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    final_text: str = ""
    done_seen: bool = False

    def absorb(self, ev: ChatEvent) -> None:
        """Apply ev до акумулятора. Error-event'и caller обробляє сам — тут
        ми лише накопичуємо стан до моменту Done/Drop."""
        match ev:
            case TokenEvent(delta=delta):
                self.buffer += delta
            case ToolCallEvent(name=name, args=args):
                self.tool_calls.append({"name": name, "args": args})
            case ToolResultEvent(attachments=tool_files):
                self.attachments.extend(tool_files)
            case DoneEvent(final_text=ft):
                self.final_text = ft
                self.done_seen = True
