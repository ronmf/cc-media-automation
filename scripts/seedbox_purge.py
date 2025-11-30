#!/usr/bin/env python3
"""Intelligent seedbox torrent purge using hash-based matching.

This script deletes torrents from the seedbox after they have been imported
to Radarr/Sonarr and meet seeding policy requirements (ratio and/or age).

Instead of unreliable filename matching, this script:
1. Queries Radarr/Sonarr for imported downloads (via downloadId field)
2. Queries rtorrent for seeding torrents (via XMLRPC)
3. Cross-references by hash (downloadId == torrent hash)
4. Deletes torrents that meet policy requirements

Policy:
- Delete if imported AND (ratio >= min_ratio OR age >= min_days)
- Keep if not imported OR (ratio < min_ratio AND age < min_days)

Features:
- Hash-based matching (100% accurate)
- Flexible seeding policy (ratio OR age)
- rtorrent XMLRPC integration
- Comprehensive logging and error handling
- ntfy notifications
- Dry-run mode for safety

Usage:
    # Dry-run mode (default - shows what would be deleted)
    python3 scripts/seedbox_purge.py --dry-run

    # Execute mode (actually delete torrents)
    python3 scripts/seedbox_purge.py --execute

    # Verbose output
    python3 scripts/seedbox_purge.py --dry-run --verbose

    # Custom config file
    python3 scripts/seedbox_purge.py --execute --config /path/to/config.yaml
"""

import sys
import argparse
import time
from pathlib import Path
from typing import Dict, Set, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import acquire_lock
from utils.rtorrent_client import RTorrentClient
from utils.api_clients import RadarrAPI, SonarrAPI


def get_imported_hashes(radarr: RadarrAPI, sonarr: SonarrAPI, logger) -> Set[str]:
    """Get all imported torrent hashes from Radarr/Sonarr.

    The key insight: downloadId field in Radarr/Sonarr history IS the torrent hash!

    Args:
        radarr: Radarr API client
        sonarr: Sonarr API client
        logger: Logger instance

    Returns:
        Set of torrent hashes (lowercase for case-insensitive matching)
    """
    imported = set()

    # Get Radarr history (eventType=3 means "Downloaded/Imported")
    logger.info("Getting Radarr import history...")
    try:
        radarr_history = radarr._request(
            'GET',
            '/api/v3/history',
            params={'eventType': 3, 'pageSize': 1000}
        )

        if radarr_history and 'records' in radarr_history:
            for record in radarr_history['records']:
                download_id = record.get('downloadId', '').lower()
                if download_id:
                    imported.add(download_id)

        logger.info(f"Found {len([h for h in imported])} imported movies")

    except Exception as e:
        logger.warning(f"Could not get Radarr history: {e}")

    # Get Sonarr history
    logger.info("Getting Sonarr import history...")
    try:
        sonarr_history = sonarr._request(
            'GET',
            '/api/v3/history',
            params={'eventType': 3, 'pageSize': 1000}
        )

        initial_count = len(imported)

        if sonarr_history and 'records' in sonarr_history:
            for record in sonarr_history['records']:
                download_id = record.get('downloadId', '').lower()
                if download_id:
                    imported.add(download_id)

        logger.info(f"Found {len(imported) - initial_count} imported episodes")

    except Exception as e:
        logger.warning(f"Could not get Sonarr history: {e}")

    logger.info(f"Total unique imported hashes: {len(imported)}")
    return imported


def meets_policy(torrent: Dict, min_ratio: float, min_days: int) -> Tuple[bool, str]:
    """Check if torrent meets deletion policy.

    Policy: Delete if ratio >= min_ratio OR age >= min_days

    Args:
        torrent: Torrent info dict (must have 'ratio' and 'timestamp_finished')
        min_ratio: Minimum ratio requirement
        min_days: Minimum seeding days requirement

    Returns:
        Tuple of (should_delete, reason)
    """
    ratio = torrent['ratio']
    finished_ts = torrent['timestamp_finished']

    # Calculate age in days
    if finished_ts > 0:
        age_seconds = time.time() - finished_ts
        age_days = age_seconds / 86400
    else:
        age_days = 0

    # Check policy (ratio OR age, not AND)
    if ratio >= min_ratio:
        return True, f"ratio {ratio:.2f} >= {min_ratio}"

    if age_days >= min_days:
        return True, f"age {age_days:.1f} days >= {min_days}"

    return False, f"ratio {ratio:.2f}, age {age_days:.1f} days"


def purge_torrents(config: dict, dry_run: bool = False, verbose: bool = False) -> bool:
    """Purge torrents from seedbox based on policy.

    Args:
        config: Configuration dictionary
        dry_run: If True, don't actually delete torrents
        verbose: If True, show detailed information

    Returns:
        True if purge successful, False otherwise
    """
    logger = setup_logging('seedbox_purge.log', level=config['logging']['level'])
    notifier = create_notifier(config)

    logger.info("="*60)
    logger.info("SEEDBOX TORRENT PURGE STARTED")
    logger.info("="*60)

    if dry_run:
        logger.info("DRY-RUN MODE: Torrents will NOT be deleted")
    else:
        logger.info("EXECUTE MODE: Torrents WILL be deleted")

    # Get policy settings
    min_ratio = config['thresholds'].get('seedbox_min_ratio', 1.5)
    min_days = config['thresholds'].get('seedbox_age_days', 2)

    logger.info(f"Policy: ratio >= {min_ratio} OR age >= {min_days} days")

    try:
        # Acquire lock
        with acquire_lock('seedbox_purge'):
            logger.info("Lock acquired, proceeding with purge")

            # Initialize API clients
            logger.info("Initializing API clients...")

            # RTorrent client
            rtorrent = RTorrentClient(
                host="nl3864.dediseedbox.com",  # Use hostname, not IP
                username=config['seedbox']['username'],
                password=config['seedbox']['password']
            )

            # Test connection
            try:
                rtorrent.test_connection()
            except Exception as e:
                error_msg = f"Failed to connect to rtorrent: {e}"
                logger.error(error_msg)
                notifier.notify_error('seedbox_purge', error_msg)
                return False

            # Radarr/Sonarr clients
            radarr = RadarrAPI(
                config['radarr']['url'],
                config['radarr']['api_key']
            )

            sonarr = SonarrAPI(
                config['sonarr']['url'],
                config['sonarr']['api_key']
            )

            # Get imported hashes from Radarr/Sonarr
            logger.info("Getting imported hashes from Radarr/Sonarr...")
            imported_hashes = get_imported_hashes(radarr, sonarr, logger)

            if not imported_hashes:
                logger.warning("No imported hashes found. Nothing to do.")
                logger.info("Make sure Radarr/Sonarr are configured and have download history.")
                return True

            # Get seeding torrents from rtorrent
            logger.info("Getting seeding torrents from rtorrent...")
            try:
                seeding_hashes = rtorrent.get_seeding_torrents()
                logger.info(f"Found {len(seeding_hashes)} seeding torrents")
            except Exception as e:
                error_msg = f"Failed to get seeding torrents: {e}"
                logger.error(error_msg)
                notifier.notify_error('seedbox_purge', error_msg)
                return False

            # Get global stats
            try:
                stats = rtorrent.get_global_stats()
                if stats:
                    logger.info(
                        f"Bandwidth: ↓ {stats['down_rate']/1024:.1f} KB/s, "
                        f"↑ {stats['up_rate']/1024:.1f} KB/s"
                    )
            except:
                pass

            # Cross-reference and check policy
            logger.info("Cross-referencing hashes and checking policy...")
            logger.info("")

            deleted_count = 0
            kept_count = 0
            not_imported_count = 0
            total_size_deleted = 0

            for hash_id in seeding_hashes:
                # Check if imported (case-insensitive)
                if hash_id.lower() not in imported_hashes:
                    if verbose:
                        logger.info(f"⏭️  SKIP (not imported): {hash_id[:8]}...")
                    not_imported_count += 1
                    continue

                # Get torrent info
                try:
                    torrent = rtorrent.get_torrent_info(hash_id)
                except Exception as e:
                    logger.warning(f"Could not get info for {hash_id}: {e}")
                    continue

                # Check policy
                should_delete, reason = meets_policy(torrent, min_ratio, min_days)

                if should_delete:
                    size_gb = torrent['size_bytes'] / (1024 ** 3)

                    if dry_run:
                        logger.info(
                            f"🗑️  [DRY-RUN] Would delete: {torrent['name']}\n"
                            f"    Reason: {reason}\n"
                            f"    Size: {size_gb:.2f} GB\n"
                            f"    Hash: {hash_id}"
                        )
                    else:
                        logger.info(
                            f"🗑️  Deleting: {torrent['name']}\n"
                            f"    Reason: {reason}\n"
                            f"    Size: {size_gb:.2f} GB"
                        )

                        try:
                            rtorrent.delete_torrent(hash_id, delete_files=True)
                            logger.info("    ✅ Deleted successfully")
                            total_size_deleted += torrent['size_bytes']
                        except Exception as e:
                            logger.error(f"    ❌ Failed to delete: {e}")

                    deleted_count += 1
                else:
                    if verbose:
                        logger.info(
                            f"✅ KEEP: {torrent['name']}\n"
                            f"    Reason: {reason}"
                        )
                    kept_count += 1

                # Add spacing between torrents
                if verbose or should_delete:
                    logger.info("")

            # Summary
            logger.info("="*60)
            logger.info("PURGE SUMMARY")
            logger.info("="*60)
            logger.info(f"Imported in Radarr/Sonarr:  {len(imported_hashes)}")
            logger.info(f"Seeding on seedbox:         {len(seeding_hashes)}")
            logger.info(f"Not imported (kept):        {not_imported_count}")
            logger.info(f"Policy not met (kept):      {kept_count}")
            logger.info(f"Policy met (deleted):       {deleted_count}")

            if deleted_count > 0:
                total_gb = total_size_deleted / (1024 ** 3)
                logger.info(f"Space freed:                {total_gb:.2f} GB")

            # Dry-run reminder
            if dry_run and deleted_count > 0:
                logger.info("")
                logger.info("💡 This was a dry run. To actually delete:")
                logger.info("   python3 scripts/seedbox_purge.py --execute")

            # Success notification
            if deleted_count > 0 and not dry_run:
                if config['notifications']['ntfy']['send_on_success']:
                    notifier.notify_success(
                        'seedbox_purge',
                        f'Deleted {deleted_count} torrents',
                        stats={
                            'deleted': deleted_count,
                            'kept': kept_count,
                            'space_freed_gb': f"{total_gb:.2f}" if deleted_count > 0 else "0"
                        }
                    )

            logger.info("="*60)
            logger.info("SEEDBOX PURGE COMPLETED SUCCESSFULLY")
            logger.info("="*60)

            return True

    except Exception as e:
        error_msg = f"Unexpected error during purge: {e}"
        logger.exception(error_msg)
        notifier.notify_error('seedbox_purge', error_msg, details=str(e))
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Intelligent seedbox torrent purge using hash-based matching',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (default - safe mode, shows what would be deleted)
  %(prog)s --dry-run

  # Execute (actually delete torrents)
  %(prog)s --execute

  # Verbose output (show all torrents, not just deletions)
  %(prog)s --dry-run --verbose

  # Use custom config file
  %(prog)s --execute --config /path/to/config.yaml

Policy:
  Torrents are deleted if they meet ALL of these conditions:
  1. Have been imported to Radarr/Sonarr (verified by hash)
  2. Meet seeding requirements:
     - ratio >= seedbox_min_ratio (default: 1.5)
     OR
     - age >= seedbox_age_days (default: 2 days)

Notes:
  - Uses hash-based matching (100%% accurate, no filename issues)
  - Connects to rtorrent via XMLRPC (HTTP Digest auth)
  - Cross-references Radarr/Sonarr downloadId with torrent hash
  - Lock file prevents concurrent execution
  - Logs to logs/seedbox_purge.log
  - Sends ntfy notifications on errors and deletions
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
        help='Execute mode: actually delete torrents'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed information (all torrents, not just deletions)'
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
    success = purge_torrents(config, dry_run=dry_run, verbose=args.verbose)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
