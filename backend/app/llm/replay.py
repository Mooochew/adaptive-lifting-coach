"""Demo mode: replay recorded AI replies, and record new ones from the live model.

Rehearse a demo once with DEMO_MODE=true and a real model: every reply is saved under
DEMO_DIR, keyed by the exact request (model + reply schema + system prompt + user message, so retries are
recorded too). Running the same steps again replays them instantly, identically and for free, with
or without a network. A request that was never recorded goes to the live model (and is recorded);
with no live model it's "unavailable", so the lifter gets the standard plan or no note.

Recordings hold what was typed during the rehearsal, so the folder is gitignored.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.base import LLMClient, LLMUnavailable, LLMUsage

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)


class ReplayLLM:
    def __init__(self, folder: Path | str, live: LLMClient | None, model: str = ""):
        self.folder = Path(folder)
        self.live = live
        self.model = model      # part of the key: another model's recordings are never replayed as this one's
        self.name = f"replay+{live.name}" if live else "replay"
        self.usage = live.usage if live else LLMUsage()

    def _path(self, system: str, user: str, schema: type[BaseModel]) -> Path:
        key = hashlib.sha256(f"{self.model}\n{schema.__name__}\n{system}\n{user}".encode()).hexdigest()[:32]
        return self.folder / f"{schema.__name__}-{key}.json"

    def generate(self, *, system: str, user: str, schema: type[T], timeout_s: float | None = None) -> T:
        path = self._path(system, user, schema)
        if path.exists():
            try:
                return schema.model_validate(json.loads(path.read_text(encoding="utf-8"))["reply"])
            except (OSError, ValueError, KeyError, ValidationError):
                log.warning("Unreadable demo recording %s; asking the live model again", path.name)
        if self.live is None:
            raise LLMUnavailable("no recording for this request (demo mode, no live model)")
        reply = self.live.generate(system=system, user=user, schema=schema, timeout_s=timeout_s)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"schema": schema.__name__, "model": self.live.name, "user": user,
                                        "reply": reply.model_dump(mode="json")}, indent=2), encoding="utf-8")
        except OSError:
            log.warning("Couldn't save demo recording %s", path.name)
        return reply
