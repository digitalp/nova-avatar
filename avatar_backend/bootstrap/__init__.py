"""Bootstrap package — app container, startup, shutdown, lifecycle."""
from avatar_backend.bootstrap.container import AppContainer  # noqa: F401
from avatar_backend.bootstrap.lifecycle import Lifecycle  # noqa: F401
from avatar_backend.bootstrap.startup import bootstrap  # noqa: F401
from avatar_backend.bootstrap.shutdown import teardown  # noqa: F401
from avatar_backend.bootstrap.background import schedule_background_tasks  # noqa: F401
