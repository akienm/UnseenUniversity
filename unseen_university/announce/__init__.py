"""
unseen_university.announce — capability announce protocol (v1).

An agent plugs in by sending an IdentityEnvelope to comms://announce.
The AnnounceBroker resolves the agent's profile, assembles a Manifest,
and the AnnounceListener publishes the reply on comms://announce-events.
"""

from .broker import AnnounceBroker, AnnounceError, ManifestAssembler
from .client import (
    AnnounceRejectedError,
    AnnounceTimeoutError,
    DatacenterClient,
)
from .envelope import ANNOUNCE_MAILBOX, IdentityEnvelope, ValidationError
from .announce_mcp import AnnounceMcpServer
from .idle_loop import AnnounceIdleLoop
from .igor_shim import IgorShim
from .invalidator import Invalidator
from .listener import AnnounceListener
from .manifest import ANNOUNCE_EVENTS_MAILBOX, MANIFEST_SCHEMA_VERSION, Manifest
from .profile import (
    DEFAULT_PROFILES_DIR,
    ProfileNotFoundError,
    ProfileValidationError,
    load_profile,
    profile_yaml_etag,
)

__all__ = [
    "ANNOUNCE_EVENTS_MAILBOX",
    "ANNOUNCE_MAILBOX",
    "AnnounceBroker",
    "AnnounceError",
    "AnnounceIdleLoop",
    "AnnounceListener",
    "AnnounceMcpServer",
    "AnnounceRejectedError",
    "AnnounceTimeoutError",
    "DEFAULT_PROFILES_DIR",
    "DatacenterClient",
    "IdentityEnvelope",
    "IgorShim",
    "Invalidator",
    "MANIFEST_SCHEMA_VERSION",
    "Manifest",
    "ManifestAssembler",
    "ProfileNotFoundError",
    "ProfileValidationError",
    "ValidationError",
    "load_profile",
    "profile_yaml_etag",
]
