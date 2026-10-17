"""Screen capture + vision for the local desktop (CDX local tool).

Gives the agent actual eyes on the machine's display — the HUD's "identify
what's under you" use case. Captures the X11 screen (optionally a region)
with ImageMagick's ``import`` and runs the frame through the existing
auxiliary vision router via ``vision_analyze_tool``.

Linux/X11 only, by design: this host (DGX Spark) runs GNOME on X11. The
capture includes everything on screen — including the HUD bar itself if it
overlaps the region — so callers wanting "what is under the HUD" should pass
the region beside/around the bar, or just ask about the full screen.
"""
import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from typing import Optional

from tools.registry import registry
from tools.vision_tools import check_vision_requirements, vision_analyze_tool

logger = logging.getLogger(__name__)

_CAPTURE_TIMEOUT_S = 15


def _display() -> Optional[str]:
    """The X display to capture: the session's, else the console default."""
    d = os.environ.get("DISPLAY")
    if d:
        return d
    # Headless-spawned backends (systemd gateways) don't inherit the desktop
    # session env; fall back to the console X server.
    for candidate in (":1", ":0"):
        if os.path.exists(f"/tmp/.X11-unix/X{candidate[1:]}"):
            return candidate
    return None


def check_screen_capture_requirements() -> bool:
    return (
        os.uname().sysname == "Linux"
        and shutil.which("import") is not None
        and _display() is not None
        and check_vision_requirements()
    )


async def screen_look_tool(
    question: str,
    region: Optional[list] = None,
    task_id: Optional[str] = None,
) -> str:
    """Capture the screen (or a region) and answer a question about it."""
    display = _display()
    if display is None:
        return json.dumps({"success": False, "error": "No X display available to capture."})

    out = os.path.join(
        tempfile.gettempdir(), f"hermes-screen-look-{int(time.time() * 1000)}.png"
    )
    cmd = ["import", "-window", "root"]
    if region:
        try:
            x, y, w, h = (int(v) for v in region)
            cmd += ["-crop", f"{w}x{h}+{x}+{y}"]
        except (TypeError, ValueError):
            return json.dumps(
                {"success": False, "error": "region must be [x, y, width, height] integers"}
            )
    cmd.append(out)

    env = dict(os.environ)
    env["DISPLAY"] = display
    # The gdm Xauthority covers the console session when the backend env has none.
    env.setdefault("XAUTHORITY", f"/run/user/{os.getuid()}/gdm/Xauthority")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_CAPTURE_TIMEOUT_S)
        if proc.returncode != 0 or not os.path.exists(out):
            return json.dumps({
                "success": False,
                "error": f"screen capture failed: {(stderr or b'').decode(errors='replace')[:300]}",
            })
    except asyncio.TimeoutError:
        return json.dumps({"success": False, "error": "screen capture timed out"})

    try:
        prompt = (
            "This is a screenshot of the user's desktop"
            + (" (cropped region)" if region else "")
            + f". Answer the user's question about what is visible: {question}"
        )
        return await vision_analyze_tool(image_url=out, user_prompt=prompt, task_id=task_id)
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


SCREEN_LOOK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "screen_look",
        "description": (
            "Look at the machine's own display (X11 screenshot + vision analysis) and "
            "answer a question about what is currently on screen. Use when the user "
            "asks what is on/under/behind a window, to identify UI on the desktop, or "
            "to read something visible on their screen. Optionally restrict to a "
            "region [x, y, width, height] in screen pixels."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What to identify or answer about the current screen contents.",
                },
                "region": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional [x, y, width, height] crop in screen pixels.",
                },
            },
            "required": ["question"],
        },
    },
}


async def _handle_screen_look(args: dict, **kw) -> str:
    return await screen_look_tool(
        question=args.get("question", "Describe what is on screen."),
        region=args.get("region"),
        task_id=kw.get("task_id"),
    )


registry.register(
    name="screen_look",
    toolset="vision",
    schema=SCREEN_LOOK_SCHEMA,
    handler=_handle_screen_look,
    check_fn=check_screen_capture_requirements,
    is_async=True,
    emoji="🖥️",
)
