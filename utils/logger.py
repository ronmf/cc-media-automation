"""Centralized logging with rotation and retention management.

This module provides a standardized logging setup with:
- Rotating file handlers (10MB max per file)
- 30-day log retention
- Console and file output
- Consistent formatting across all scripts

Example:
    >>> from utils.logger import setup_logging
    >>> logger = setup_logging('my_script.log')
    >>> logger.info('Script started')
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional


def cleanup_old_logs(log_dir: str, retention_days: int = 30) -> int:
    """Remove log files older than retention period.

    Args:
        log_dir: Directory containing log files
        retention_days: Number of days to keep logs (default: 30)

    Returns:
        Number of log files deleted

    Example:
        >>> deleted = cleanup_old_logs('/mnt/media/scripts/logs', 30)
        >>> print(f"Deleted {deleted} old log files")
    """
    if not os.path.exists(log_dir):
        return 0

    cutoff_date = datetime.now() - timedelta(days=retention_days)
    deleted_count = 0

    try:
        for filename in os.listdir(log_dir):
            if not filename.endswith('.log'):
                continue

            filepath = os.path.join(log_dir, filename)

            # Check file modification time
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))

            if mtime < cutoff_date:
                try:
                    os.remove(filepath)
                    deleted_count += 1
                    print(f"Deleted old log file: {filename}")
                except Exception as e:
                    print(f"Warning: Could not delete {filename}: {e}")

    except Exception as e:
        print(f"Warning: Error during log cleanup: {e}")

    return deleted_count


def setup_logging(
    log_file: str,
    level: str = 'INFO',
    log_dir: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 10,
    retention_days: int = 30,
    console: bool = True
) -> logging.Logger:
    """Setup logging with file rotation and retention.

    Creates a logger that writes to both file and console with automatic
    rotation and cleanup of old log files.

    Args:
        log_file: Name of the log file (e.g., 'seedbox_sync.log')
        level: Logging level ('DEBUG', 'INFO', 'WARNING', 'ERROR')
        log_dir: Directory for log files (default: inferred from config)
        max_bytes: Maximum size per log file before rotation (default: 10MB)
        backup_count: Number of backup files to keep (default: 10)
        retention_days: Days to keep log files (default: 30)
        console: Whether to also log to console (default: True)

    Returns:
        Configured logger instance

    Example:
        >>> logger = setup_logging('my_script.log', level='DEBUG')
        >>> logger.info('Processing started')
        >>> logger.error('An error occurred')
    """
    # Determine log directory
    if log_dir is None:
        # Try to infer from common locations
        script_dir = Path(__file__).parent.parent
        log_dir = script_dir / 'logs'
    else:
        log_dir = Path(log_dir)

    # Create log directory if it doesn't exist
    log_dir.mkdir(parents=True, exist_ok=True)

    # Full path to log file
    log_path = log_dir / log_file

    # Cleanup old logs
    cleanup_old_logs(str(log_dir), retention_days)

    # Create logger
    logger_name = Path(log_file).stem  # Use filename without extension
    logger = logging.getLogger(logger_name)

    # Clear any existing handlers
    logger.handlers.clear()

    # Set logging level
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler (optional)
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get an existing logger by name.

    Args:
        name: Logger name (usually script name without .py)

    Returns:
        Logger instance

    Example:
        >>> logger = get_logger('seedbox_sync')
        >>> logger.info('Using existing logger')
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager for temporary log level changes.

    Example:
        >>> logger = setup_logging('script.log')
        >>> logger.info('Normal logging')
        >>> with LogContext(logger, 'DEBUG'):
        ...     logger.debug('This will be logged')
        >>> logger.debug('This will not be logged')
    """

    def __init__(self, logger: logging.Logger, level: str):
        """Initialize context manager.

        Args:
            logger: Logger instance
            level: Temporary log level
        """
        self.logger = logger
        self.new_level = getattr(logging, level.upper())
        self.old_level = logger.level

    def __enter__(self):
        """Set temporary log level."""
        self.logger.setLevel(self.new_level)
        return self.logger

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Restore original log level."""
        self.logger.setLevel(self.old_level)


# Example usage
if __name__ == '__main__':
    import time

    # Setup logging
    logger = setup_logging('test.log', level='INFO')

    logger.info('Testing logger setup')
    logger.debug('This debug message will not appear (level=INFO)')
    logger.warning('This is a warning')
    logger.error('This is an error')

    # Test log context for temporary debug
    logger.info('Before context')
    with LogContext(logger, 'DEBUG'):
        logger.debug('This debug message WILL appear (temporary DEBUG level)')
    logger.debug('This debug message will not appear (back to INFO)')

    # Test rotation by writing large amount of data
    logger.info('Testing log rotation...')
    for i in range(100):
        logger.info(f'Test message {i} - ' + 'x' * 1000)

    logger.info('Logger test complete')

    # Test cleanup
    print('\nTesting log cleanup...')
    deleted = cleanup_old_logs('logs', retention_days=0)  # Delete all logs for testing
    print(f'Deleted {deleted} log files')
