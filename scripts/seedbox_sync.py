#!/usr/bin/env python3
"""Seedbox synchronization script with lftp over SFTP.

This script syncs downloads from the seedbox to local storage using lftp
over SFTP (SSH File Transfer Protocol). It mirrors files from the remote
/downloads directory to the local downloads/_done directory.

Features:
- lftp mirror over SFTP (secure SSH connection)
- Parallel downloads with pget (multiple connections per file)
- Real-time download progress in CLI
- Exclude unwanted files (*.meta)
- Remove source files after successful transfer
- Clean up *.lftp temp files
- Lock file to prevent concurrent execution
- Comprehensive logging and error handling
- ntfy notifications on errors

Usage:
    # Dry-run mode (default - shows what would be done)
    python3 scripts/seedbox_sync.py --dry-run

    # Execute mode (actually sync files)
    python3 scripts/seedbox_sync.py --execute

    # Custom config file
    python3 scripts/seedbox_sync.py --execute --config /path/to/config.yaml

Note:
    dediseedbox.com uses SFTP (SSH protocol on port 40685), not FTP.
    The script automatically uses sftp:// protocol for connection.
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


def build_lftp_command(config: dict, dry_run: bool = False) -> str:
    """Build lftp mirror command from configuration.

    Args:
        config: Configuration dictionary
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
    if not dry_run:
        mirror_opts += " --Remove-source-files --Remove-source-dirs"

    cmd += f'mirror {mirror_opts} --log="{lftp_log}" '
    cmd += f'"{sb["remote_downloads"]}" "{paths["downloads_done"]}"\n'
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

            # Build lftp command
            lftp_cmd = build_lftp_command(config, dry_run)

            logger.info(f"Syncing from seedbox: {config['seedbox']['host']}")
            logger.info(f"Remote: {config['seedbox']['remote_downloads']}")
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
