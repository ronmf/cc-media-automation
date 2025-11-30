#!/usr/bin/env python3
"""Seedbox purge script - clean old files via SSH.

This script connects to the seedbox via SSH and removes files older than
a configured threshold (default: 2 days). It verifies that files exist
locally before deleting them remotely to prevent data loss.

Features:
- SSH connection with password authentication
- Verify local files exist before remote deletion
- Verify file sizes match (within 1% tolerance)
- Monitor disk usage and warn when approaching limit
- Never delete protected folders (/_ready, /.recycle)
- Comprehensive logging and error handling
- ntfy notifications for warnings and errors

Usage:
    # Dry-run mode (default - shows what would be deleted)
    python3 scripts/seedbox_purge.py --dry-run

    # Execute mode (actually delete files)
    python3 scripts/seedbox_purge.py --execute

    # Custom config file
    python3 scripts/seedbox_purge.py --execute --config /path/to/config.yaml
"""

import sys
import argparse
import os
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import (
    acquire_lock,
    verify_file_exists,
    verify_file_size_match,
    is_protected_folder
)
from utils.seedbox_ssh import SeedboxSSH, SeedboxError


def purge_seedbox(config: dict, dry_run: bool = False) -> bool:
    """Purge old files from seedbox after verifying local copies exist.

    Args:
        config: Configuration dictionary
        dry_run: If True, don't actually delete files

    Returns:
        True if purge successful, False otherwise
    """
    logger = setup_logging('seedbox_purge.log', level=config['logging']['level'])
    notifier = create_notifier(config)

    logger.info("="*60)
    logger.info("SEEDBOX PURGE STARTED")
    logger.info("="*60)

    if dry_run:
        logger.info("DRY-RUN MODE: Files will NOT be deleted")
    else:
        logger.info("EXECUTE MODE: Files WILL be deleted")

    try:
        # Acquire lock
        with acquire_lock('seedbox_purge'):
            logger.info("Lock acquired, proceeding with purge")

            # Connect to seedbox
            sb = config['seedbox']
            logger.info(f"Connecting to seedbox: {sb['host']}:{sb['port']}")

            with SeedboxSSH(
                host=sb['host'],
                port=sb['port'],
                username=sb['username'],
                password=sb['password']
            ) as ssh:
                logger.info("SSH connection established")

                # Check disk usage
                usage = ssh.get_disk_usage()
                logger.info(
                    f"Disk usage: {usage['used_gb']:.1f} GB / {usage['total_gb']:.1f} GB "
                    f"({usage['percent_used']:.1f}%)"
                )

                # Warn if approaching limit
                warn_threshold = config['thresholds']['seedbox_max_gb']
                if usage['used_gb'] > warn_threshold:
                    warning_msg = (
                        f"Seedbox disk usage ({usage['used_gb']:.1f} GB) exceeds "
                        f"warning threshold ({warn_threshold} GB)"
                    )
                    logger.warning(warning_msg)
                    notifier.notify_warning(
                        'seedbox_purge',
                        warning_msg,
                        recommendation='Run purge immediately or check for large files'
                    )

                # List old files
                age_days = config['thresholds']['seedbox_age_days']
                logger.info(f"Finding files older than {age_days} days...")

                remote_files = ssh.list_files(
                    sb['remote_downloads'],
                    older_than_days=age_days
                )

                logger.info(f"Found {len(remote_files)} files older than {age_days} days")

                # Process each file
                deleted_count = 0
                skipped_count = 0
                total_size_deleted = 0
                protected_folders = config['safety']['protected_folders']

                for remote_file in remote_files:
                    remote_path = remote_file['path']
                    remote_size = remote_file['size']

                    # Check if in protected folder
                    if is_protected_folder(remote_path, protected_folders):
                        logger.info(f"PROTECTED: {remote_path}")
                        skipped_count += 1
                        continue

                    # Build local path
                    # Remote: /downloads/file.mkv -> Local: /mnt/media/downloads/_done/file.mkv
                    remote_rel = remote_path.replace(sb['remote_downloads'], '').lstrip('/')
                    local_path = os.path.join(config['paths']['downloads_done'], remote_rel)

                    # Verify local file exists
                    if not verify_file_exists(local_path):
                        logger.warning(f"SKIP (not local): {remote_path}")
                        skipped_count += 1
                        continue

                    # Verify size matches
                    if not verify_file_size_match(
                        local_path,
                        remote_size,
                        tolerance=config['safety']['size_tolerance']
                    ):
                        logger.warning(f"SKIP (size mismatch): {remote_path}")
                        skipped_count += 1
                        continue

                    # Safe to delete
                    size_gb = remote_size / (1024 ** 3)

                    if dry_run:
                        logger.info(f"[DRY-RUN] Would delete: {remote_path} ({size_gb:.2f} GB)")
                    else:
                        try:
                            ssh.delete_file(remote_path)
                            logger.info(f"DELETED: {remote_path} ({size_gb:.2f} GB)")
                            deleted_count += 1
                            total_size_deleted += remote_size
                        except SeedboxError as e:
                            logger.error(f"Failed to delete {remote_path}: {e}")
                            skipped_count += 1

                # Clean up empty directories
                if not dry_run and deleted_count > 0:
                    logger.info("Cleaning up empty directories...")
                    empty_dirs = ssh.delete_empty_directories(sb['remote_downloads'])
                    logger.info(f"Removed {empty_dirs} empty directories")

                # Summary
                logger.info("="*60)
                logger.info("PURGE SUMMARY")
                logger.info("="*60)
                logger.info(f"Files found: {len(remote_files)}")
                logger.info(f"Files deleted: {deleted_count}")
                logger.info(f"Files skipped: {skipped_count}")

                if deleted_count > 0:
                    total_gb = total_size_deleted / (1024 ** 3)
                    logger.info(f"Space freed: {total_gb:.2f} GB")

                # Success notification (if files were deleted)
                if deleted_count > 0 and config['notifications']['ntfy']['send_on_success']:
                    notifier.notify_success(
                        'seedbox_purge',
                        f'Purged {deleted_count} old files',
                        stats={
                            'files_deleted': deleted_count,
                            'space_freed_gb': f"{total_gb:.2f}",
                            'mode': 'DRY-RUN' if dry_run else 'EXECUTE'
                        }
                    )

                logger.info("="*60)
                logger.info("SEEDBOX PURGE COMPLETED SUCCESSFULLY")
                logger.info("="*60)

                return True

    except SeedboxError as e:
        error_msg = f"Seedbox connection error: {e}"
        logger.error(error_msg)
        notifier.notify_error('seedbox_purge', error_msg)
        return False

    except Exception as e:
        error_msg = f"Unexpected error during purge: {e}"
        logger.exception(error_msg)
        notifier.notify_error('seedbox_purge', error_msg, details=str(e))
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Purge old files from seedbox after verifying local copies',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (default - safe mode, shows what would be deleted)
  %(prog)s --dry-run

  # Execute (actually delete files)
  %(prog)s --execute

  # Use custom config file
  %(prog)s --execute --config /path/to/config.yaml

Notes:
  - Only deletes files older than configured threshold (default: 2 days)
  - Verifies local copy exists before deleting remote
  - Verifies file sizes match (within 1%% tolerance)
  - Never deletes protected folders (/_ready, /.recycle)
  - Warns if disk usage exceeds threshold (default: 700 GB)
  - Lock file prevents concurrent execution
  - Logs to logs/seedbox_purge.log
  - Sends ntfy notifications on errors and warnings
        """
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry-run mode: show what would be deleted (safe)'
    )
    mode_group.add_argument(
        '--execute',
        action='store_true',
        help='Execute mode: actually delete files'
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )

    args = parser.parse_args()

    # Determine mode
    dry_run = args.dry_run

    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"ERROR: Failed to load configuration: {e}", file=sys.stderr)
        return 1

    # Run purge
    success = purge_seedbox(config, dry_run=dry_run)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
