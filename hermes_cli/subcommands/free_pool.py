"""``hermes free-pool`` subcommand parser.

Mirrors ``hermes_cli/subcommands/prompt_size.py``: registers the top-level
``free-pool`` (alias ``fp``) subcommand and binds dispatch to ``cmd_free_pool``
via ``set_defaults(func=...)``.
"""

from typing import Callable


def build_free_pool_parser(subparsers, *, cmd_free_pool: Callable) -> None:
    """Attach the ``free-pool`` subcommand to ``subparsers``."""

    # free-pool command
    free_pool_parser = subparsers.add_parser(
        "free-pool",
        help="Show OpenCode Zen free-pool status (live models, health, circuit-break, last outage)",
        aliases=("fp",),
    )
    free_pool_parser.add_argument(
        "--probe",
        action="store_true",
        help="force a live probe instead of showing the cached pool",
    )
    free_pool_parser.set_defaults(func=cmd_free_pool)
