"""
Sensory tools - Igor's interface to the physical world.

Capabilities:
  get_datetime()       - Current date, time, day of week, timezone
  take_photo()         - Webcam snapshot → workspace/
  record_audio()       - Mic recording → workspace/

WSL2 notes:
  - Camera: requires usbipd-win USB passthrough. Will fail gracefully without it.
  - Audio:  requires 'sudo apt install libportaudio2' and sounddevice pip package.
            WSLg provides PulseAudio so mic may work. Will fail gracefully without it.

On native Linux all three work without special setup.
"""

import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .registry import Tool, registry

WORKSPACE = Path(__file__).parent.parent.parent / "workspace"


# ── Date / Time ───────────────────────────────────────────────────────────────

def get_datetime() -> str:
    """Return the current date, time, day of week, and timezone."""
    tz_name = os.getenv("TZ", "")
    try:
        tz = ZoneInfo(tz_name) if tz_name else None
    except ZoneInfoNotFoundError:
        tz = None

    now = datetime.now(tz)
    tz_label = now.strftime("%Z") if tz else "local time (TZ not set)"
    return (
        f"{now.strftime('%A, %B %d, %Y')}  "
        f"{now.strftime('%I:%M:%S %p')}  "
        f"({tz_label})"
    )


# ── Camera ────────────────────────────────────────────────────────────────────

def take_photo(filename: str = "") -> str:
    """
    Capture a single frame from the default webcam and save it to workspace/.

    Returns the file path on success, or an error message explaining the failure.
    On WSL2 without usbipd-win USB passthrough, the camera is not accessible.
    """
    try:
        import cv2  # type: ignore
    except ImportError:
        return "Error: opencv-python not installed. Run: pip install opencv-python"

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return (
            "Camera not available. On WSL2 this requires usbipd-win USB passthrough. "
            "On native Linux, check that a webcam is connected and /dev/video0 exists."
        )

    try:
        ret, frame = cap.read()
        if not ret or frame is None:
            return "Camera opened but failed to capture frame."

        WORKSPACE.mkdir(exist_ok=True)
        name = filename.strip() or f"photo_{uuid.uuid4().hex[:8]}.jpg"
        if not name.endswith((".jpg", ".png")):
            name += ".jpg"
        out_path = WORKSPACE / name
        cv2.imwrite(str(out_path), frame)
        h, w = frame.shape[:2]
        return f"Photo saved: workspace/{name}  ({w}×{h} px)"
    finally:
        cap.release()


# ── Audio ─────────────────────────────────────────────────────────────────────

def record_audio(seconds: int = 5, filename: str = "") -> str:
    """
    Record audio from the default microphone and save as a WAV file in workspace/.

    Returns the file path on success, or an error message.
    Requires: sudo apt install libportaudio2  and  pip install sounddevice scipy
    On WSL2, WSLg provides PulseAudio so this may work if the mic is accessible.
    """
    try:
        import sounddevice as sd  # type: ignore
        import scipy.io.wavfile as wav  # type: ignore
        import numpy as np
    except ImportError as e:
        return (
            f"Audio libraries not fully installed ({e}). "
            "Run: sudo apt install libportaudio2  then  pip install sounddevice scipy"
        )

    seconds = max(1, min(seconds, 60))  # Clamp 1-60 seconds
    sample_rate = 44100

    try:
        audio = sd.rec(
            int(seconds * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
        )
        sd.wait()
    except Exception as e:
        return (
            f"Audio recording failed: {e}. "
            "On WSL2, ensure WSLg is active and the microphone is accessible via PulseAudio."
        )

    WORKSPACE.mkdir(exist_ok=True)
    name = filename.strip() or f"audio_{uuid.uuid4().hex[:8]}.wav"
    if not name.endswith(".wav"):
        name += ".wav"
    out_path = WORKSPACE / name

    try:
        wav.write(str(out_path), sample_rate, audio)
    except Exception as e:
        return f"Failed to write WAV file: {e}"

    return f"Audio saved: workspace/{name}  ({seconds}s @ {sample_rate}Hz)"


# ── Register tools ─────────────────────────────────────────────────────────────

registry.register(Tool(
    name="get_datetime",
    description="Get the current date, time, and day of week. Use this whenever you need to know what time or day it is.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=get_datetime,
))

registry.register(Tool(
    name="take_photo",
    description=(
        "Capture a snapshot from the webcam and save it to workspace/. "
        "Returns the file path. May not work on WSL2 without USB passthrough."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Optional filename (default: auto-generated). Will be saved in workspace/."},
        },
        "required": [],
    },
    fn=take_photo,
))

registry.register(Tool(
    name="record_audio",
    description=(
        "Record a short audio clip from the microphone and save as WAV in workspace/. "
        "Requires libportaudio2 system library and sounddevice+scipy pip packages."
    ),
    parameters={
        "type": "object",
        "properties": {
            "seconds": {"type": "integer", "description": "Recording length in seconds (1-60, default 5)"},
            "filename": {"type": "string", "description": "Optional filename (default: auto-generated). Will be saved in workspace/."},
        },
        "required": [],
    },
    fn=record_audio,
))
