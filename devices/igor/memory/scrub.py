"""
Credential scrubber — prevent API keys and secrets from leaving the machine.

scrub(text) → text with credential-like patterns replaced by [REDACTED-CREDENTIAL].

Patterns caught:
  - Prefixed keys: sk-, gh[pors]_, AIza, AKIA, xox[bpas]-
  - Authorization: Bearer <token>
  - Assignment: api_key=value, token=value, password=value, secret=value
  - Pure hex strings ≥32 chars (MD5/SHA/API key style)
  - Base64-ish runs ≥40 chars not part of URL/path

Design: pure Python, no dependencies, synchronous, deterministic.
Called at all write boundaries (cortex store/ring/twm) and before
upstream API calls (anthropic.py, openrouter_reasoner.py).
"""

import re

_REDACTED = "[REDACTED-CREDENTIAL]"

# Full-match replacement patterns (entire match → _REDACTED)
_FULL_PATTERNS: list[re.Pattern] = [
    # OpenAI / Anthropic keys
    re.compile(r'sk-[A-Za-z0-9_\-]{20,}'),
    # GitHub tokens (personal, oauth, server-to-server, etc.)
    re.compile(r'gh[pors]_[A-Za-z0-9]{30,}'),
    # Google API keys
    re.compile(r'AIza[A-Za-z0-9_\-]{30,}'),
    # AWS access keys
    re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
    # Slack tokens
    re.compile(r'xox[bpas]-[A-Za-z0-9\-]{20,}'),
    # Pure lowercase hex ≥32 chars (MD5/SHA/UUID-like API keys)
    # Excluded: path segments (preceded/followed by / or word chars) to avoid
    # clobbering SHA-256 cache filenames and training corpus book IDs (#30)
    re.compile(r'(?<![/\w])[0-9a-f]{32,}(?![/\w])'),
    # Pure uppercase hex ≥32 chars
    re.compile(r'(?<![/\w])[0-9A-F]{32,}(?![/\w])'),
    # Base64/URL-safe-base64-ish tokens ≥40 chars.
    # '/' excluded from charset — real API tokens use URL-safe base64 ('-'/'_'); including
    # '/' caused path segments to be absorbed and mangled (e.g. .TheIgors/cache/... redacted).
    re.compile(r'(?<![/\w])[A-Za-z0-9+\-_]{40,}={0,2}(?![/\w])'),
]

# Bearer token — replace only the token portion (group 1), keep "Bearer "
_BEARER_RE = re.compile(r'(?i)(bearer\s+)([A-Za-z0-9\-._~+/]{20,}=*)')

# Assignment pattern — replace only the value (group 2), keep key name
_ASSIGNMENT_RE = re.compile(
    r'(?i)'
    r'((?:api[-_]?key|api[-_]?secret|secret|token|password|passwd|credentials?|auth[-_]?key)'
    r'\s*[=:]\s*)'
    r'(\S{12,})'
)


def scrub(text: str) -> str:
    """Replace credential-like patterns in text with [REDACTED-CREDENTIAL]."""
    if not text or not isinstance(text, str):
        return text
    for pattern in _FULL_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    text = _BEARER_RE.sub(lambda m: m.group(1) + _REDACTED, text)
    text = _ASSIGNMENT_RE.sub(lambda m: m.group(1) + _REDACTED, text)
    return text
