from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .backend_client import GenerationBackendClient
from .jobs import GenerationJobStore


@dataclass(frozen=True)
class GenerationDependencies:
    workspace_root: Path
    backend_client: GenerationBackendClient
    job_store: GenerationJobStore
