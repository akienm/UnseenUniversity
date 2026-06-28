"""
LoggingControlCenter — hierarchical log routing for unseen_university devices.

Log tree layout (rooted at $UNSEEN_UNIVERSITY_HOME/logs/):
  logs/
    rack/                  # skeleton, bus, registry
      rack.log
    Igor-wild-0001/        # Igor subsystem logs
      cognition/
        Igor-wild-0001.log
      memory/
        Igor-wild-0001.log
    claude_code/           # CC logs
      claude_code.log
    CC.0/                  # comms-channel logs (chat history written by export_chat)
    Shared/                # comms-channel logs

Each component gets a directory named after its system or comms-channel address.
Subsystems nest one level deeper. All log files use RotatingFileHandler (10MB,
5 backups).

Default root is $UNSEEN_UNIVERSITY_HOME/logs/ (see config.device_config).
Override by calling configure(custom_root) before first use.

Usage:
    from unseen_university.logcontrol.control_center import LoggingControlCenter
    # Uses $UNSEEN_UNIVERSITY_HOME/logs/ automatically:
    log = LoggingControlCenter.instance().get_logger('Igor-wild-0001', 'cognition')
    log.info('cognition cycle complete')
    # Or with explicit root:
    LoggingControlCenter.configure(Path('/tmp/test-logs'))
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


class LoggingControlCenter:
    _instance: LoggingControlCenter | None = None
    _root: Path | None = None

    def __init__(self, root: Path) -> None:
        self._root = root

    @classmethod
    def default_root(cls) -> Path:
        """Return the default log root from $UNSEEN_UNIVERSITY_HOME/logs/."""
        from unseen_university.config.device_config import unseen_university_logs

        return unseen_university_logs()

    @classmethod
    def configure(cls, root: Path) -> LoggingControlCenter:
        """Set the log root and return the singleton instance."""
        cls._instance = cls(root)
        return cls._instance

    @classmethod
    def instance(cls) -> LoggingControlCenter:
        if cls._instance is None:
            cls._instance = cls(cls.default_root())
        return cls._instance

    def get_logger(
        self, device_id: str, subsystem: str | None = None
    ) -> logging.Logger:
        """
        Return a Python logger wired to the correct datacenter_logs path.

        Logger name: '{device_id}.{subsystem}' or '{device_id}' if no subsystem.
        Log file:    datacenter_logs/{device_id}/{subsystem}/{device_id}.log
                     or datacenter_logs/{device_id}/{device_id}.log
        """
        if subsystem:
            log_dir = self._root / device_id / subsystem
            logger_name = f"{device_id}.{subsystem}"
        else:
            log_dir = self._root / device_id
            logger_name = device_id

        logger = logging.getLogger(logger_name)
        if logger.handlers:
            return logger  # already configured in this process

        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{device_id}.log"

        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger
