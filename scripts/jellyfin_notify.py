#!/usr/bin/env python3
"""Jellyfin notification script - trigger library refresh after imports.

This script polls Radarr and Sonarr for recent imports and triggers a
Jellyfin library refresh to make new content available immediately.

Features:
- Poll Radarr/Sonarr history for recent imports
- Trigger full Jellyfin library scan (no user IDs needed)
- Track last check time with checkpoint file
- Can be run manually or via cron (every 10-15 minutes)
- Comprehensive logging

Usage:
    # Dry-run mode (default - shows what would be done)
    python3 scripts/jellyfin_notify.py --dry-run

    # Execute mode (actually trigger Jellyfin refresh)
    python3 scripts/jellyfin_notify.py --execute

    # Force refresh (ignore checkpoint, refresh anyway)
    python3 scripts/jellyfin_notify.py --execute --force

    # Custom config file
    python3 scripts/jellyfin_notify.py --execute --config /path/to/config.yaml
"""

import sys
import argparse
import os
import json
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import acquire_lock
from utils.api_clients import RadarrAPI, SonarrAPI, JellyfinAPI, APIError


CHECKPOINT_FILE = 'jellyfin_notify_checkpoint.json'


def load_checkpoint(checkpoint_path: str) -> datetime:
    """Load last check time from checkpoint file.

    Args:
        checkpoint_path: Path to checkpoint file

    Returns:
        Last check time, or 1 hour ago if no checkpoint exists
    """
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, 'r') as f:
                data = json.load(f)
                return datetime.fromisoformat(data['last_check'])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    # Default to 1 hour ago if no checkpoint
    return datetime.now() - timedelta(hours=1)


def save_checkpoint(checkpoint_path: str, check_time: datetime) -> None:
    """Save checkpoint with last check time.

    Args:
        checkpoint_path: Path to checkpoint file
        check_time: Time to save
    """
    with open(checkpoint_path, 'w') as f:
        json.dump({
            'last_check': check_time.isoformat(),
            'last_check_formatted': check_time.strftime('%Y-%m-%d %H:%M:%S')
        }, f, indent=2)


def check_recent_imports(config: dict, since: datetime, logger) -> bool:
    """Check if there are recent imports in Radarr or Sonarr.

    Args:
        config: Configuration dictionary
        since: Check for imports since this time
        logger: Logger instance

    Returns:
        True if recent imports found, False otherwise
    """
    has_imports = False

    # Check Radarr
    try:
        logger.info("Checking Radarr for recent imports...")
        radarr = RadarrAPI(config['radarr']['url'], config['radarr']['api_key'])

        # Get all recent history events (no filter, will filter client-side)
        # Note: Radarr v3 API has changed eventType filtering
        history = radarr.get_history()

        # Filter for import events since last check
        # eventType: 3 = DownloadFolderImported
        recent_events = []
        for event in history:
            # Filter by event type (3 = DownloadFolderImported)
            if event.get('eventType') != 'downloadFolderImported':
                continue

            event_date = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
            if event_date.replace(tzinfo=None) > since:
                recent_events.append(event)

        if recent_events:
            logger.info(f"Found {len(recent_events)} recent Radarr imports")
            for event in recent_events[:5]:  # Show first 5
                movie_title = event.get('movie', {}).get('title', 'Unknown')
                logger.info(f"  - {movie_title}")
            has_imports = True
        else:
            logger.info("No recent Radarr imports")

    except APIError as e:
        logger.warning(f"Failed to check Radarr history: {e}")

    # Check Sonarr
    try:
        logger.info("Checking Sonarr for recent imports...")
        sonarr = SonarrAPI(config['sonarr']['url'], config['sonarr']['api_key'])

        # Get all recent history events (no filter, will filter client-side)
        # Note: Sonarr v3 API has changed eventType filtering
        history = sonarr.get_history()

        # Filter for import events since last check
        recent_events = []
        for event in history:
            # Filter by event type (downloadFolderImported)
            if event.get('eventType') != 'downloadFolderImported':
                continue

            event_date = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
            if event_date.replace(tzinfo=None) > since:
                recent_events.append(event)

        if recent_events:
            logger.info(f"Found {len(recent_events)} recent Sonarr imports")
            for event in recent_events[:5]:  # Show first 5
                series_title = event.get('series', {}).get('title', 'Unknown')
                episode_title = event.get('episode', {}).get('title', '')
                logger.info(f"  - {series_title}: {episode_title}")
            has_imports = True
        else:
            logger.info("No recent Sonarr imports")

    except APIError as e:
        logger.warning(f"Failed to check Sonarr history: {e}")

    return has_imports


def notify_jellyfin(config: dict, dry_run: bool = False, force: bool = False) -> bool:
    """Trigger Jellyfin library refresh if there are recent imports.

    Args:
        config: Configuration dictionary
        dry_run: If True, don't actually trigger refresh
        force: If True, trigger refresh regardless of recent imports

    Returns:
        True if successful, False otherwise
    """
    logger = setup_logging('jellyfin_notify.log', level=config['logging']['level'])
    notifier = create_notifier(config)

    logger.info("="*60)
    logger.info("JELLYFIN NOTIFY STARTED")
    logger.info("="*60)

    if dry_run:
        logger.info("DRY-RUN MODE: Jellyfin will NOT be refreshed")
    else:
        logger.info("EXECUTE MODE: Jellyfin WILL be refreshed if needed")

    if force:
        logger.info("FORCE MODE: Will refresh regardless of recent imports")

    try:
        # Acquire lock
        with acquire_lock('jellyfin_notify'):
            logger.info("Lock acquired, proceeding with check")

            # Load checkpoint
            checkpoint_path = os.path.join(config['paths']['scripts'], CHECKPOINT_FILE)
            last_check = load_checkpoint(checkpoint_path)
            logger.info(f"Last check: {last_check.strftime('%Y-%m-%d %H:%M:%S')}")

            # Check for recent imports
            if force:
                logger.info("Force mode: skipping import check")
                has_imports = True
            else:
                has_imports = check_recent_imports(config, last_check, logger)

            # Trigger Jellyfin refresh if needed
            if has_imports:
                logger.info("Recent imports found, triggering Jellyfin library refresh...")

                if dry_run:
                    logger.info("[DRY-RUN] Would trigger Jellyfin library refresh")
                else:
                    try:
                        jellyfin = JellyfinAPI(
                            config['jellyfin']['url'],
                            config['jellyfin']['api_key']
                        )

                        jellyfin.refresh_library()
                        logger.info("✓ Jellyfin library refresh triggered successfully")

                        # Update checkpoint
                        now = datetime.now()
                        save_checkpoint(checkpoint_path, now)
                        logger.info(f"Checkpoint updated: {now.strftime('%Y-%m-%d %H:%M:%S')}")

                    except APIError as e:
                        error_msg = f"Failed to trigger Jellyfin refresh: {e}"
                        logger.error(error_msg)
                        notifier.notify_error('jellyfin_notify', error_msg)
                        return False

            else:
                logger.info("No recent imports, skipping Jellyfin refresh")

                # Still update checkpoint
                if not dry_run:
                    now = datetime.now()
                    save_checkpoint(checkpoint_path, now)
                    logger.info(f"Checkpoint updated: {now.strftime('%Y-%m-%d %H:%M:%S')}")

            logger.info("="*60)
            logger.info("JELLYFIN NOTIFY COMPLETED SUCCESSFULLY")
            logger.info("="*60)

            return True

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.exception(error_msg)
        notifier.notify_error('jellyfin_notify', error_msg, details=str(e))
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Trigger Jellyfin library refresh after Radarr/Sonarr imports',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (default - safe mode, shows what would be done)
  %(prog)s --dry-run

  # Execute (trigger refresh if recent imports found)
  %(prog)s --execute

  # Force refresh (ignore checkpoint, refresh anyway)
  %(prog)s --execute --force

  # Use custom config file
  %(prog)s --execute --config /path/to/config.yaml

Notes:
  - Checks Radarr/Sonarr for recent imports since last run
  - Triggers full Jellyfin library scan if imports found
  - Saves checkpoint to track last check time
  - Designed to run via cron every 10-15 minutes
  - Lock file prevents concurrent execution
  - Logs to logs/jellyfin_notify.log

Cron example (every 10 minutes):
  */10 * * * * cd /mnt/media/scripts && python3 scripts/jellyfin_notify.py --execute
        """
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry-run mode: check imports but do NOT refresh Jellyfin (safe)'
    )
    mode_group.add_argument(
        '--execute',
        action='store_true',
        help='Execute mode: refresh Jellyfin if recent imports found'
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Force refresh regardless of recent imports (requires --execute)'
    )

    args = parser.parse_args()

    # Validate force flag
    if args.force and args.dry_run:
        print("ERROR: --force requires --execute mode", file=sys.stderr)
        return 1

    # Determine mode
    dry_run = args.dry_run

    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"ERROR: Failed to load configuration: {e}", file=sys.stderr)
        return 1

    # Run notification
    success = notify_jellyfin(config, dry_run=dry_run, force=args.force)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
