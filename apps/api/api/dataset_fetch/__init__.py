"""Dataset Fetch Orchestration API – package entry point.

Re-exports the public symbols so that ``from api.dataset_fetch import router``
and similar continue to work after the monolith was decomposed.

Orchestrator imports are guarded so that lightweight submodule imports
(e.g., in tests) don't require FastAPI to be installed.
"""
from .models import DATASET_DEFINITIONS, FetchContext  # noqa: F401
from .utils import _load_project_context  # noqa: F401
from .job_state import DatasetJobState, recover_orphaned_jobs, cleanup_orphaned_staging  # noqa: F401

from .research.api import router  # noqa: F401
