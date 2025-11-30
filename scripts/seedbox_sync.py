#!/usr/bin/env python3
"""Seedbox synchronization script with lftp over SFTP.

This script syncs downloads from the seedbox to local storage using lftp
over SFTP (SSH File Transfer Protocol). It intelligently checks multiple
remote directories in priority order and syncs only when files are available.

Features:
- Multi-directory checking with priority fallback (read from config.yaml)
  1. /downloads/_ready - completed torrents only (primary)
  2. /downloads - all downloads including unfinished (fallback)
- Pre-check for files before running lftp (prevents false errors on empty directories)
- lftp mirror over SFTP (secure SSH connection)
- Parallel downloads with pget (multiple connections per file)
- Real-time download progress in CLI
- Exclude unwanted files (*.meta)
- Remove source files after successful transfer
- Clean up *.lftp temp files
- Lock file to prevent concurrent execution
- Comprehensive logging and error handling
- ntfy notifications on errors (not sent when directories are empty)

Usage:
    # Dry-run mode (default - shows what would be done)
    python3 scripts/seedbox_sync.py --dry-run

    # Execute mode (actually sync files)
    python3 scripts/seedbox_sync.py --execute

    # Custom config file
    python3 scripts/seedbox_sync.py --execute --config /path/to/config.yaml

Configuration (config.yaml):
    seedbox:
      remote_downloads: "/downloads/_ready"  # Primary (completed torrents)
      remote_downloads_fallback: "/downloads"  # Fallback (all torrents)

Note:
    dediseedbox.com uses SFTP (SSH protocol on port 40685), not FTP.
    The script automatically uses sftp:// protocol for connection.

    If all remote directories are empty or don't exist, the script exits
    successfully without running lftp, preventing false error notifications.

    Priority: /_ready is checked first (completed only), /downloads is fallback.
"""

import sys
import argparse
import subprocess
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import acquire_lock, cleanup_temp_files
from utils.seedbox_ssh import SeedboxSSH, SeedboxError


def check_remote_has_files(config: dict, logger) -> tuple[bool, str]:
    """Check if remote directories have any files to sync.

    Checks directories in priority order:
    1. remote_downloads (_ready folder) - completed torrents only
    2. remote_downloads_fallback (/downloads) - includes unfinished torrents

    Args:
        config: Configuration dictionary
        logger: Logger instance

    Returns:
        Tuple of (has_files: bool, sync_path: str)
        - has_files: True if any directory has files
        - sync_path: Path to use for syncing (priority directory with files)
    """
    sb = config['seedbox']

    # Define directories to check in priority order
    directories_to_check = []

    # Primary: /_ready (completed torrents)
    if 'remote_downloads' in sb:
        directories_to_check.append({
            'path': sb['remote_downloads'],
            'description': 'completed torrents (_ready)'
        })

    # Fallback: /downloads (all torrents, including unfinished)
    if 'remote_downloads_fallback' in sb:
        directories_to_check.append({
            'path': sb['remote_downloads_fallback'],
            'description': 'all downloads (including unfinished)'
        })

    if not directories_to_check:
        logger.error("No remote download paths configured in config.yaml")
        return False, ""

    logger.info(f"Checking {len(directories_to_check)} remote directories for files...")

    try:
        with SeedboxSSH(
            host=sb['host'],
            port=sb['port'],
            username=sb['username'],
            password=sb['password']
        ) as ssh:
            # Check each directory in priority order
            for dir_info in directories_to_check:
                remote_path = dir_info['path']
                description = dir_info['description']

                logger.info(f"Checking {remote_path} ({description})...")

                # Check if directory exists first
                if not ssh.path_exists(remote_path):
                    logger.info(f"  Directory does not exist: {remote_path}")
                    continue

                # Use find to check for any files (not directories)
                # Returns first file found, or empty if none
                cmd = f'find "{remote_path}" -type f -print -quit'
                stdout, stderr, exit_code = ssh.execute_command(cmd)

                if exit_code != 0:
                    logger.warning(f"  Failed to check directory: {stderr}")
                    # Don't fail-safe here, continue to next directory
                    continue

                # If stdout is not empty, files found
                if stdout.strip():
                    logger.info(f"  ✓ Found files in {remote_path}")
                    return True, remote_path
                else:
                    logger.info(f"  Directory is empty: {remote_path}")

            # No files found in any directory
            logger.info("No files to sync in any remote directory")
            return False, ""

    except SeedboxError as e:
        logger.warning(f"SSH connection failed while checking remote directories: {e}")
        # On connection error, assume there might be files (fail safe)
        # Use primary remote_downloads path
        return True, sb.get('remote_downloads', '/downloads/_ready')
    except Exception as e:
        logger.warning(f"Unexpected error checking remote directories: {e}")
        # On unexpected error, assume there might be files (fail safe)
        return True, sb.get('remote_downloads', '/downloads/_ready')


def build_lftp_command(config: dict, sync_path: str, dry_run: bool = False) -> str:
    """Build lftp mirror command from configuration.

    Args:
        config: Configuration dictionary
        sync_path: Remote path to sync from
        dry_run: If True, don't actually delete remote files

    Returns:
        lftp command string
    """
    sb = config['seedbox']
    paths = config['paths']
    lftp = sb['lftp']

    # Get project root directory (parent of scripts/)
    project_root = Path(__file__).parent.parent

    # Create logs directory relative to project root
    logs_dir = project_root / 'logs'
    logs_dir.mkdir(exist_ok=True)

    # Build log file path relative to project
    lftp_log = logs_dir / 'seedbox_sync_lftp.log'

    # Build lftp command using SFTP protocol (dediseedbox uses SSH)
    cmd = f"""lftp -u {sb['username']},{sb['password']} sftp://{sb['host']}:{sb['port']} << 'EOF'
set sftp:auto-confirm yes
set net:timeout {lftp['timeout']}
set net:max-retries {lftp['max_retries']}
set net:reconnect-interval-base {lftp['reconnect_interval_base']}
set net:reconnect-interval-multiplier {lftp['reconnect_interval_multiplier']}
set mirror:use-pget-n {lftp['pget_connections']}
set pget:min-chunk-size {lftp['min_chunk_size']}
set xfer:use-temp-file {'on' if lftp['use_temp_files'] else 'off'}
set xfer:temp-file-name *{lftp['temp_suffix']}
"""

    # Mirror command
    mirror_opts = f"-P {lftp['parallel_files']} -c -v"

    # Exclude unwanted files (regex pattern)
    mirror_opts += ' --exclude ".*\\.meta$"'

    # Only remove source files if not in dry-run mode
    # IMPORTANT: Do NOT use --Remove-source-dirs as it deletes the _ready folder itself!
    # We only want to delete individual files, not the parent directories
    if not dry_run:
        mirror_opts += " --Remove-source-files"

    cmd += f'mirror {mirror_opts} --log="{lftp_log}" '
    cmd += f'"{sync_path}" "{paths["downloads_done"]}"\n'
    cmd += "quit\nEOF"

    return cmd


def sync_seedbox(config: dict, dry_run: bool = False) -> bool:
    """Synchronize files from seedbox to local storage.

    Args:
        config: Configuration dictionary
        dry_run: If True, don't remove source files

    Returns:
        True if sync successful, False otherwise
    """
    logger = setup_logging('seedbox_sync.log', level=config['logging']['level'])
    notifier = create_notifier(config)

    logger.info("="*60)
    logger.info("SEEDBOX SYNC STARTED")
    logger.info("="*60)

    if dry_run:
        logger.info("DRY-RUN MODE: Remote files will NOT be deleted")
    else:
        logger.info("EXECUTE MODE: Remote files WILL be deleted after sync")

    try:
        # Acquire lock to prevent concurrent execution
        with acquire_lock('seedbox_sync'):
            logger.info("Lock acquired, proceeding with sync")

            # Check if remote directories have files before running lftp
            logger.info("Checking remote directories for files...")
            has_files, sync_path = check_remote_has_files(config, logger)

            if not has_files:
                logger.info("No files to sync from seedbox - all remote directories are empty")
                logger.info("="*60)
                logger.info("SEEDBOX SYNC COMPLETED (NO FILES TO SYNC)")
                logger.info("="*60)

                print("\n" + "="*60)
                print("NO FILES TO SYNC")
                print("="*60)
                print("All remote directories are empty - nothing to download")
                print("="*60 + "\n")

                return True  # Success - empty directories are not an error

            # Build lftp command with the discovered sync path
            lftp_cmd = build_lftp_command(config, sync_path, dry_run)

            logger.info(f"Syncing from seedbox: {config['seedbox']['host']}")
            logger.info(f"Remote: {sync_path}")
            logger.info(f"Local: {config['paths']['downloads_done']}")

            # Execute lftp with real-time output
            logger.info("Starting lftp mirror...")
            print("\n" + "="*60)
            print("LFTP TRANSFER IN PROGRESS")
            print("="*60 + "\n")

            result = subprocess.run(
                lftp_cmd,
                shell=True,
                text=True,
                timeout=3600  # 1 hour timeout
            )

            print("\n" + "="*60)

            # Check result
            if result.returncode != 0:
                error_msg = f"lftp failed with exit code {result.returncode}"
                logger.error(error_msg)
                print(f"\nERROR: {error_msg}")

                notifier.notify_error(
                    'seedbox_sync',
                    error_msg,
                    details="Check logs/seedbox_sync_lftp.log for details"
                )
                return False

            logger.info("lftp mirror completed successfully")
            print("LFTP TRANSFER COMPLETED")
            print("="*60 + "\n")

            # Clean up temp files (*.lftp)
            if not dry_run:
                logger.info("Cleaning up temporary files...")
                temp_pattern = f"*{config['seedbox']['lftp']['temp_suffix']}"
                deleted_count = cleanup_temp_files(
                    config['paths']['downloads_done'],
                    temp_pattern
                )
                logger.info(f"Cleaned up {deleted_count} temporary files")

            # Success notification (if enabled)
            if config['notifications']['ntfy']['send_on_success']:
                notifier.notify_success(
                    'seedbox_sync',
                    'Download complete',
                    stats={
                        'mode': 'DRY-RUN' if dry_run else 'EXECUTE'
                    }
                )

            logger.info("="*60)
            logger.info("SEEDBOX SYNC COMPLETED SUCCESSFULLY")
            logger.info("="*60)

            return True

    except subprocess.TimeoutExpired:
        error_msg = "lftp timeout after 1 hour"
        logger.error(error_msg)
        notifier.notify_error('seedbox_sync', error_msg)
        return False

    except Exception as e:
        error_msg = f"Unexpected error during sync: {e}"
        logger.exception(error_msg)
        notifier.notify_error('seedbox_sync', error_msg, details=str(e))
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Sync downloads from seedbox to local storage',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (default - safe mode, shows what would be synced)
  %(prog)s --dry-run

  # Execute (actually sync and delete remote files)
  %(prog)s --execute

  # Use custom config file
  %(prog)s --execute --config /path/to/config.yaml

Notes:
  - In dry-run mode, files are synced but NOT deleted from seedbox
  - In execute mode, files are deleted from seedbox after successful transfer
  - Lock file prevents concurrent execution
  - Logs to logs/seedbox_sync.log
  - Sends ntfy notifications on errors
        """
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry-run mode: sync files but do NOT delete from seedbox (safe)'
    )
    mode_group.add_argument(
        '--execute',
        action='store_true',
        help='Execute mode: sync files AND delete from seedbox'
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

    # Run sync
    success = sync_seedbox(config, dry_run=dry_run)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
