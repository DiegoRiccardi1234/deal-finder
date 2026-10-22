"""Forma di risposta condivisa dai client OpenAI-compatible e dall'adapter Anthropic."""

from collections.abc import Sequence
from typing import Protocol


class CompletionMessage(Protocol):
    @property
    def content(self) -> str | None: ...


class CompletionChoice(Protocol):
    @property
    def message(self) -> CompletionMessage: ...

    @property
    def finish_reason(self) -> str | None: ...


class CompletionResponse(Protocol):
    @property
    def choices(self) -> Sequence[CompletionChoice]: ...
