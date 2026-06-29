"""
Pass-through relay (change.41).

Igor acts as a transparent relay between akien and any cloud inference model.
Messages pass through; Igor monitors budget/arbiter/heartbeat but does not inject personality.

Commands (handled in main.py):
    /relay start MODEL      — enter relay mode with specified reasoner
    /relay end              — exit relay, store transcript, offer summary + extraction
    /relay extract          — pull last fenced code or JSON block from transcript
    /relay send claudecode  — send extracted block to Claude Code CLI via subprocess
"""

import subprocess
from datetime import datetime
from typing import Optional
from ..igor_base import IgorBase


class RelaySession(IgorBase):
    """Maintains state for an active relay conversation."""

    def __init__(self, model_name: str, inference=None):
        self.model_name   = model_name
        self._inference   = inference
        self.messages: list[dict] = []   # [{role, content, ts}]
        self.started_at   = datetime.now().isoformat()
        self.last_extract: Optional[str] = None

    def _get_inference(self):
        """Lazy-load the canonical Inference Proxy (injectable for tests)."""
        if getattr(self, "_inference", None) is None:
            from unseen_university.devices.inference.device import InferenceDevice

            self._inference = InferenceDevice()
        return self._inference

    def send(self, user_input: str) -> str:
        """Forward user_input to the relay model. Returns response text.

        Relay is the experiment exception: it names a specific model — but, like
        every consumer, it goes through the Inference Proxy (req.model), never a
        raw reasoner. Pure relay: no memory or core patterns injected.
        """
        self.messages.append({
            "role": "user",
            "content": user_input,
            "ts": datetime.now().isoformat(),
        })
        try:
            from unseen_university.devices.inference.shim import InferenceRequest

            resp = self._get_inference().dispatch(
                InferenceRequest(
                    messages=[{"role": "user", "content": user_input}],
                    model=self.model_name,
                    task_class="analyst",
                    foreground=True,
                    instance_id="relay",
                )
            )
            text = resp.text
        except Exception as e:
            text = f"[relay error: {e}]"

        self.messages.append({
            "role": "assistant",
            "content": text,
            "ts": datetime.now().isoformat(),
        })
        return text

    def extract_last_block(self) -> Optional[str]:
        """
        Find the last fenced code block or JSON block in assistant messages.
        Returns the block text, or None if nothing found.
        """
        for msg in reversed(self.messages):
            if msg["role"] != "assistant":
                continue
            content = msg["content"]

            # Prefer fenced code blocks
            start = content.rfind("```")
            if start != -1:
                end = content.rfind("```", start + 3)
                if end > start:
                    block = content[start: end + 3]
                    self.last_extract = block
                    return block

            # Fall back to outermost JSON object
            start = content.find("{")
            end   = content.rfind("}")
            if start != -1 and end > start:
                block = content[start: end + 1]
                self.last_extract = block
                return block

        return None

    def summary(self) -> str:
        """Short summary of the relay session."""
        turns = sum(1 for m in self.messages if m["role"] == "user")
        lines = [
            f"Relay with {self.model_name}",
            f"Started:  {self.started_at[:16]}",
            f"Turns:    {turns}",
            f"Messages: {len(self.messages)}",
        ]
        if self.messages:
            last_user = next(
                (m["content"][:100] for m in reversed(self.messages) if m["role"] == "user"),
                "(none)",
            )
            lines.append(f"Last question: {last_user}")
        return "\n".join(lines)

    def transcript_csb(self) -> str:
        """Format transcript as a compact CSB string for LTM storage."""
        parts = []
        for m in self.messages:
            role = "U" if m["role"] == "user" else "A"
            ts   = m["ts"][11:16] if len(m["ts"]) >= 16 else m["ts"]
            parts.append(f"{role}[{ts}]: {m['content'][:200]}")
        return "\n".join(parts)


def send_to_claude_code(block: str) -> str:
    """
    Send extracted block to Claude Code CLI via subprocess.
    Tries `claude --print '{block}'` if claude is in PATH.
    Returns output text or an error message.
    """
    try:
        result = subprocess.run(
            ["claude", "--print", block],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return result.stdout.strip() or "(no output from Claude Code)"
        return (
            f"claude --print exited {result.returncode}:\n"
            f"{result.stderr[:300]}"
        )
    except FileNotFoundError:
        return (
            "Claude Code CLI not found in PATH.\n"
            "Install it with: npm install -g @anthropic-ai/claude-code"
        )
    except subprocess.TimeoutExpired:
        return "Claude Code CLI timed out after 60s."
    except Exception as e:
        return f"Error running Claude Code CLI: {e}"
