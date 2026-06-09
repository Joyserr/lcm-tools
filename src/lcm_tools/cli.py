"""LCM CLI - ROS2-like command line tools for LCM middleware.

Provides the ``lcm`` command with subcommands:
  lcm topic echo <channel>   — View real-time topic data
  lcm topic list             — List active topics (channels)
  lcm topic stats            — Monitor topic statistics
  lcm node list              — List discovered publisher nodes
"""

from __future__ import annotations

from typing import Optional

import typer

from lcm_tools.commands.node_list import node_app
from lcm_tools.commands.topic_echo import echo
from lcm_tools.commands.topic_list import list_channels
from lcm_tools.commands.topic_stats import stats

# ---------------------------------------------------------------------------
# Root application
# ---------------------------------------------------------------------------
app = typer.Typer(
    name="lcm",
    help="LCM command line tools — inspect and monitor LCM networks.\n\n"
    "Similar to ROS2 CLI tools (ros2 topic echo, ros2 node list, etc.) "
    "but for LCM (Lightweight Communications and Marshalling).",
    no_args_is_help=True,
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Topic subcommand group
# ---------------------------------------------------------------------------
topic_app = typer.Typer(
    help="Inspect and monitor LCM topics (channels).",
    no_args_is_help=True,
)
app.add_typer(topic_app, name="topic")

# Register topic subcommands
topic_app.command(name="echo", help="Echo messages on a channel.")(echo)
topic_app.command(name="list", help="List active channels.")(list_channels)
topic_app.command(name="stats", help="Show real-time channel statistics.")(stats)

# Node subcommand group is imported as a Typer app and attached directly
app.add_typer(node_app, name="node")


if __name__ == "__main__":
    app()
