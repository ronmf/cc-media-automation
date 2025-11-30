"""File validation and lock file utilities.

This module provides utilities for file verification, pattern matching,
and lock file management to prevent concurrent script execution.

Example:
    >>> from utils.validators import acquire_lock, verify_file_size_match
    >>> with acquire_lock('my_script'):
    ...     # Script execution protected by lock
    ...     if verify_file_size_match(local_file, remote_size):
    ...         delete_remote_file()
"""

import os
import fcntl
import re
from pathlib import Path
from typing import List, Optional
from contextlib import contextmanager
import logging


logger = logging.getLogger(__name__)


class LockError(Exception):
    """Raised when lock acquisition fails."""
    pass


@contextmanager
def acquire_lock(script_name: str, lock_dir: str = '/tmp'):
    """Acquire an exclusive lock to prevent concurrent execution.

    This context manager creates a lock file and acquires an exclusive lock
    using fcntl. If another instance is running, raises LockError.

    Args:
        script_name: Name of the script (used for lock filename)
        lock_dir: Directory for lock files (default: /tmp)

    Yields:
        File handle of the lock file

    Raises:
        LockError: If lock cannot be acquired (another instance running)

    Example:
        >>> with acquire_lock('seedbox_sync'):
        ...     # Your code here - protected from concurrent execution
        ...     sync_files()
    """
    lock_file = Path(lock_dir) / f"{script_name}.lock"

    try:
        # Open lock file
        fp = open(lock_file, 'w')

        # Try to acquire exclusive lock (non-blocking)
        try:
            fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            fp.close()
            raise LockError(f"Another instance of {script_name} is already running")

        # Write PID to lock file
        fp.write(str(os.getpid()))
        fp.flush()

        logger.debug(f"Lock acquired: {lock_file}")

        try:
            yield fp
        finally:
            # Release lock and close file
            fcntl.flock(fp, fcntl.LOCK_UN)
            fp.close()

            # Remove lock file
            try:
                lock_file.unlink()
                logger.debug(f"Lock released: {lock_file}")
            except FileNotFoundError:
                pass

    except LockError:
        raise
    except Exception as e:
        logger.error(f"Failed to acquire lock: {e}")
        raise LockError(f"Lock acquisition failed: {e}")


def verify_file_exists(path: str) -> bool:
    """Check if a file exists.

    Args:
        path: File path to check

    Returns:
        True if file exists and is a regular file

    Example:
        >>> if verify_file_exists('/mnt/media/movie.mkv'):
        ...     print("File exists")
    """
    return os.path.isfile(path)


def verify_file_size_match(
    local_path: str,
    remote_size: int,
    tolerance: float = 0.01
) -> bool:
    """Verify that local and remote file sizes match within tolerance.

    Args:
        local_path: Path to local file
        remote_size: Size of remote file in bytes
        tolerance: Acceptable size difference as fraction (default: 0.01 = 1%)

    Returns:
        True if sizes match within tolerance

    Example:
        >>> if verify_file_size_match('/mnt/media/file.mkv', 1000000000):
        ...     print("Sizes match, safe to delete remote")
    """
    if not verify_file_exists(local_path):
        logger.warning(f"Local file does not exist: {local_path}")
        return False

    try:
        local_size = os.path.getsize(local_path)

        # Check if sizes match within tolerance
        if remote_size == 0:
            return local_size == 0

        size_diff = abs(local_size - remote_size)
        relative_diff = size_diff / remote_size

        if relative_diff > tolerance:
            logger.warning(
                f"Size mismatch: local={local_size} remote={remote_size} "
                f"diff={relative_diff*100:.2f}%"
            )
            return False

        return True

    except OSError as e:
        logger.error(f"Failed to get file size for {local_path}: {e}")
        return False


def is_video_file(filename: str) -> bool:
    """Check if a file is a video file based on extension.

    Args:
        filename: Filename or path to check

    Returns:
        True if file has a video extension

    Example:
        >>> is_video_file('movie.mkv')
        True
        >>> is_video_file('subtitle.srt')
        False
    """
    video_extensions = {
        '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv',
        '.m4v', '.mpg', '.mpeg', '.m2ts', '.ts', '.vob',
        '.webm', '.ogv', '.3gp'
    }

    ext = Path(filename).suffix.lower()
    return ext in video_extensions


def is_subtitle_file(filename: str) -> bool:
    """Check if a file is a subtitle file.

    Args:
        filename: Filename or path to check

    Returns:
        True if file has a subtitle extension

    Example:
        >>> is_subtitle_file('movie.srt')
        True
    """
    subtitle_extensions = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx'}
    ext = Path(filename).suffix.lower()
    return ext in subtitle_extensions


def is_extra_file(filename: str, extra_patterns: List[str]) -> bool:
    """Check if a video file is an extra/trailer/sample based on patterns.

    Args:
        filename: Filename to check
        extra_patterns: List of regex patterns to match against

    Returns:
        True if filename matches any extra pattern

    Example:
        >>> patterns = [r'-trailer', r'-sample', r'behind.the.scenes']
        >>> is_extra_file('movie-trailer.mkv', patterns)
        True
        >>> is_extra_file('movie.mkv', patterns)
        False
    """
    for pattern in extra_patterns:
        if re.search(pattern, filename, re.IGNORECASE):
            return True
    return False


def is_metadata_file(filename: str) -> bool:
    """Check if a file is a metadata file (NFO, fanart, poster).

    Args:
        filename: Filename or path to check

    Returns:
        True if file is a metadata file

    Example:
        >>> is_metadata_file('movie.nfo')
        True
        >>> is_metadata_file('fanart.jpg')
        True
    """
    metadata_extensions = {
        '.nfo', '.jpg', '.jpeg', '.png', '.gif',  # Images
        '.tbn', '.xml'  # Kodi metadata
    }

    # Check extension
    ext = Path(filename).suffix.lower()
    if ext in metadata_extensions:
        return True

    # Check for specific metadata names
    basename = Path(filename).stem.lower()
    metadata_names = {
        'fanart', 'poster', 'banner', 'clearart', 'clearlogo',
        'discart', 'landscape', 'thumb', 'folder', 'season'
    }

    return any(name in basename for name in metadata_names)


def is_protected_folder(path: str, protected_folders: List[str]) -> bool:
    """Check if a path is in a protected folder that should never be deleted.

    Args:
        path: Path to check
        protected_folders: List of protected folder patterns

    Returns:
        True if path is in a protected folder

    Example:
        >>> protected = ['/_ready', '/.recycle']
        >>> is_protected_folder('/downloads/_ready/file.mkv', protected)
        True
    """
    for protected in protected_folders:
        if protected in path:
            return True
    return False


def get_video_files(directory: str) -> List[str]:
    """Get all video files in a directory (non-recursive).

    Args:
        directory: Directory path to scan

    Returns:
        List of video file paths

    Example:
        >>> videos = get_video_files('/mnt/media/movies/Movie (2020)')
        >>> for v in videos:
        ...     print(v)
    """
    if not os.path.isdir(directory):
        return []

    video_files = []

    try:
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)

            if os.path.isfile(filepath) and is_video_file(filename):
                video_files.append(filepath)

    except OSError as e:
        logger.error(f"Failed to list directory {directory}: {e}")
        return []

    return sorted(video_files)


def find_main_video(video_files: List[str], min_size_mb: int = 500) -> Optional[str]:
    """Find the main video file in a list (largest file above minimum size).

    Args:
        video_files: List of video file paths
        min_size_mb: Minimum size in MB to be considered main video

    Returns:
        Path to main video file, or None if no suitable file found

    Example:
        >>> videos = get_video_files('/mnt/media/movies/Movie (2020)')
        >>> main = find_main_video(videos)
        >>> print(f"Main video: {main}")
    """
    min_size_bytes = min_size_mb * 1024 * 1024

    # Filter files by minimum size
    large_files = []
    for filepath in video_files:
        try:
            size = os.path.getsize(filepath)
            if size >= min_size_bytes:
                large_files.append((filepath, size))
        except OSError:
            continue

    if not large_files:
        return None

    # Return largest file
    large_files.sort(key=lambda x: x[1], reverse=True)
    return large_files[0][0]


def cleanup_temp_files(directory: str, pattern: str = '*.lftp') -> int:
    """Remove temporary files matching a pattern.

    Args:
        directory: Directory to clean
        pattern: Glob pattern for temp files (default: '*.lftp')

    Returns:
        Number of files deleted

    Example:
        >>> deleted = cleanup_temp_files('/mnt/media/downloads/_done', '*.lftp')
        >>> print(f"Deleted {deleted} temp files")
    """
    deleted_count = 0

    try:
        temp_files = Path(directory).rglob(pattern)

        for temp_file in temp_files:
            try:
                if temp_file.is_file():
                    temp_file.unlink()
                    deleted_count += 1
                    logger.debug(f"Deleted temp file: {temp_file}")
            except OSError as e:
                logger.warning(f"Failed to delete {temp_file}: {e}")

    except Exception as e:
        logger.error(f"Failed to cleanup temp files: {e}")

    return deleted_count


# Example usage
if __name__ == '__main__':
    import time

    print("Testing validators...\n")

    # Test lock acquisition
    print("1. Testing lock acquisition:")
    try:
        with acquire_lock('test_script'):
            print("   ✓ Lock acquired")

            # Try to acquire same lock in background (should fail)
            import subprocess
            result = subprocess.run(
                ['python3', '-c',
                 "from utils.validators import acquire_lock; "
                 "with acquire_lock('test_script'): pass"],
                capture_output=True,
                timeout=2
            )
            if result.returncode != 0:
                print("   ✓ Concurrent lock prevented (expected)")

            print("   ✓ Lock will be released on exit")

    except LockError as e:
        print(f"   ✗ Lock error: {e}")

    # Test file type detection
    print("\n2. Testing file type detection:")
    test_files = [
        ('movie.mkv', is_video_file),
        ('movie-trailer.mkv', lambda f: is_video_file(f)),
        ('subtitle.srt', is_subtitle_file),
        ('movie.nfo', is_metadata_file),
        ('fanart.jpg', is_metadata_file),
    ]

    for filename, check_func in test_files:
        result = check_func(filename)
        print(f"   {filename}: {check_func.__name__} = {result}")

    # Test extra detection
    print("\n3. Testing extra file detection:")
    patterns = [r'-trailer', r'-sample', r'behind.the.scenes']
    test_extras = [
        'movie.mkv',
        'movie-trailer.mkv',
        'behind.the.scenes.mkv',
        'sample.mkv',
    ]

    for filename in test_extras:
        is_extra = is_extra_file(filename, patterns)
        print(f"   {filename}: extra = {is_extra}")

    # Test protected folder check
    print("\n4. Testing protected folder check:")
    protected = ['/_ready', '/.recycle']
    test_paths = [
        '/downloads/file.mkv',
        '/downloads/_ready/file.mkv',
        '/downloads/.recycle/file.mkv',
    ]

    for path in test_paths:
        is_protected = is_protected_folder(path, protected)
        print(f"   {path}: protected = {is_protected}")

    print("\n✓ All validator tests complete!")
