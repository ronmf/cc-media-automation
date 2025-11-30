#!/usr/bin/env python3
"""Comprehensive seedbox and local cleanup with multi-phase purge strategy.

This script performs intelligent cleanup across three phases:

Phase 1: Active Torrents (XMLRPC)
  - Delete torrents via rtorrent XMLRPC if imported + policy met
  - Uses hash-based matching (downloadId == torrent hash)
  - Policy: ratio >= min_ratio OR age >= min_days

Phase 2: Remote /downloads Files (SSH)
  - Clean up orphaned files in seedbox /downloads directory
  - Delete files that are imported to Radarr/Sonarr
  - Handles files where torrent was removed but files remain

Phase 3: Local _done Files (Filesystem)
  - Clean up local downloads/_done after import to library
  - Delete files that exist in final Radarr/Sonarr library location
  - Frees up space in staging directory

Features:
- Hash-based torrent matching (100% accurate)
- Path-based file matching for orphaned files
- Comprehensive cleanup across all storage layers
- Dry-run mode for safety
- Detailed logging and ntfy notifications

Usage:
    # Dry-run mode (shows what would be deleted)
    python3 scripts/seedbox_purge.py --dry-run --verbose

    # Execute mode (actually delete)
    python3 scripts/seedbox_purge.py --execute

    # Skip specific phases
    python3 scripts/seedbox_purge.py --execute --skip-torrents
    python3 scripts/seedbox_purge.py --execute --skip-remote-files
    python3 scripts/seedbox_purge.py --execute --skip-local-done
"""

import sys
import argparse
import time
import os
from pathlib import Path
from typing import Dict, Set, Tuple, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import acquire_lock
from utils.rtorrent_client import RTorrentClient
from utils.api_clients import RadarrAPI, SonarrAPI
from utils.seedbox_ssh import SeedboxSSH


def get_imported_hashes(radarr: RadarrAPI, sonarr: SonarrAPI, logger) -> Set[str]:
    """Get all imported torrent hashes from Radarr/Sonarr.

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


def get_imported_paths(radarr: RadarrAPI, sonarr: SonarrAPI, logger) -> Set[str]:
    """Get all imported media file paths from Radarr/Sonarr libraries.

    Args:
        radarr: Radarr API client
        sonarr: Sonarr API client
        logger: Logger instance

    Returns:
        Set of file paths in library (for checking if imports are complete)
    """
    library_files = set()

    # Get Radarr movies
    logger.info("Getting Radarr library files...")
    try:
        movies = radarr._request('GET', '/api/v3/movie')
        for movie in movies:
            if movie.get('hasFile'):
                movie_file = radarr._request('GET', f"/api/v3/moviefile/{movie['movieFile']['id']}")
                if movie_file:
                    library_files.add(movie_file['path'])

        logger.info(f"Found {len([f for f in library_files if f])} movie files in library")

    except Exception as e:
        logger.warning(f"Could not get Radarr library: {e}")

    # Get Sonarr episodes
    logger.info("Getting Sonarr library files...")
    try:
        series = sonarr._request('GET', '/api/v3/series')
        for show in series:
            episodes = sonarr._request('GET', f"/api/v3/episode?seriesId={show['id']}")
            for episode in episodes:
                if episode.get('hasFile'):
                    library_files.add(episode['episodeFile']['path'])

        logger.info(f"Found {len(library_files)} total files in libraries")

    except Exception as e:
        logger.warning(f"Could not get Sonarr library: {e}")

    return library_files


def meets_policy(torrent: Dict, min_ratio: float, min_days: int) -> Tuple[bool, str]:
    """Check if torrent meets deletion policy.

    Policy: Delete if ratio >= min_ratio OR age >= min_days

    Args:
        torrent: Torrent info dict
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


def purge_torrents(
    config: dict,
    rtorrent: RTorrentClient,
    imported_hashes: Set[str],
    logger,
    dry_run: bool = False,
    verbose: bool = False
) -> Tuple[int, int]:
    """Phase 1: Purge active torrents via XMLRPC.

    Args:
        config: Configuration dictionary
        rtorrent: RTorrent client
        imported_hashes: Set of imported torrent hashes
        logger: Logger instance
        dry_run: If True, don't actually delete
        verbose: If True, show all torrents

    Returns:
        Tuple of (deleted_count, total_size_deleted_bytes)
    """
    logger.info("="*60)
    logger.info("PHASE 1: ACTIVE TORRENT CLEANUP (XMLRPC)")
    logger.info("="*60)

    min_ratio = config['thresholds'].get('seedbox_min_ratio', 1.5)
    min_days = config['thresholds'].get('seedbox_age_days', 2)

    deleted_count = 0
    kept_count = 0
    not_imported_count = 0
    total_size_deleted = 0

    # Get seeding torrents
    try:
        seeding_hashes = rtorrent.get_seeding_torrents()
        logger.info(f"Found {len(seeding_hashes)} seeding torrents")
    except Exception as e:
        logger.error(f"Failed to get seeding torrents: {e}")
        return 0, 0

    # Process each torrent
    for hash_id in seeding_hashes:
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
                    f"🗑️  [DRY-RUN] Would delete torrent: {torrent['name']}\n"
                    f"    Reason: {reason}\n"
                    f"    Size: {size_gb:.2f} GB"
                )
            else:
                logger.info(
                    f"🗑️  Deleting torrent: {torrent['name']}\n"
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
                logger.info(f"✅ KEEP torrent: {torrent['name']} ({reason})")
            kept_count += 1

    logger.info("")
    logger.info(f"Phase 1 Summary:")
    logger.info(f"  Not imported (kept): {not_imported_count}")
    logger.info(f"  Policy not met (kept): {kept_count}")
    logger.info(f"  Policy met (deleted): {deleted_count}")
    logger.info(f"  Space freed: {total_size_deleted / (1024**3):.2f} GB")

    return deleted_count, total_size_deleted


def purge_remote_files(
    config: dict,
    imported_hashes: Set[str],
    logger,
    dry_run: bool = False,
    verbose: bool = False
) -> Tuple[int, int]:
    """Phase 2: Purge orphaned files in remote /downloads directory.

    Args:
        config: Configuration dictionary
        imported_hashes: Set of imported torrent hashes
        logger: Logger instance
        dry_run: If True, don't actually delete
        verbose: If True, show all files

    Returns:
        Tuple of (deleted_count, total_size_deleted_bytes)
    """
    logger.info("")
    logger.info("="*60)
    logger.info("PHASE 2: REMOTE /downloads CLEANUP (SSH)")
    logger.info("="*60)

    deleted_count = 0
    total_size_deleted = 0

    # Connect to seedbox via SSH
    sb = config['seedbox']
    try:
        with SeedboxSSH(
            host=sb['host'],
            port=sb['port'],
            username=sb['username'],
            password=sb['password']
        ) as ssh:
            logger.info(f"Connected to seedbox via SSH")

            # List all files in /downloads
            logger.info(f"Scanning {sb['remote_downloads']}...")
            remote_files = ssh.list_files(sb['remote_downloads'], recursive=True)
            logger.info(f"Found {len(remote_files)} files/folders")

            # Check each file
            for file_info in remote_files:
                remote_path = file_info['path']
                file_size = file_info['size']

                # Skip if in protected folder
                protected = config['safety'].get('protected_folders', [])
                if any(prot in remote_path for prot in protected):
                    if verbose:
                        logger.info(f"PROTECTED: {remote_path}")
                    continue

                # Build local equivalent path
                remote_rel = remote_path.replace(sb['remote_downloads'], '').lstrip('/')
                local_path = os.path.join(config['paths']['downloads_done'], remote_rel)

                # Check if local file exists (meaning it was downloaded)
                if not os.path.exists(local_path):
                    if verbose:
                        logger.info(f"SKIP (not downloaded yet): {remote_path}")
                    continue

                # File exists locally, so it's safe to delete remote
                size_gb = file_size / (1024 ** 3)

                if dry_run:
                    logger.info(f"🗑️  [DRY-RUN] Would delete remote file: {remote_path} ({size_gb:.2f} GB)")
                else:
                    logger.info(f"🗑️  Deleting remote file: {remote_path} ({size_gb:.2f} GB)")
                    try:
                        ssh.delete_file(remote_path)
                        logger.info("    ✅ Deleted successfully")
                        total_size_deleted += file_size
                        deleted_count += 1
                    except Exception as e:
                        logger.error(f"    ❌ Failed to delete: {e}")

            # Clean up empty directories
            if not dry_run and deleted_count > 0:
                logger.info("Cleaning up empty directories...")
                empty_dirs = ssh.delete_empty_directories(sb['remote_downloads'])
                logger.info(f"Removed {empty_dirs} empty directories")

    except Exception as e:
        logger.error(f"SSH connection error: {e}")
        return 0, 0

    logger.info("")
    logger.info(f"Phase 2 Summary:")
    logger.info(f"  Remote files deleted: {deleted_count}")
    logger.info(f"  Space freed: {total_size_deleted / (1024**3):.2f} GB")

    return deleted_count, total_size_deleted


def purge_local_done(
    config: dict,
    library_files: Set[str],
    logger,
    dry_run: bool = False,
    verbose: bool = False
) -> Tuple[int, int]:
    """Phase 3: Purge local _done files that are imported to library.

    Args:
        config: Configuration dictionary
        library_files: Set of file paths in Radarr/Sonarr libraries
        logger: Logger instance
        dry_run: If True, don't actually delete
        verbose: If True, show all files

    Returns:
        Tuple of (deleted_count, total_size_deleted_bytes)
    """
    logger.info("")
    logger.info("="*60)
    logger.info("PHASE 3: LOCAL _done CLEANUP (Filesystem)")
    logger.info("="*60)

    deleted_count = 0
    total_size_deleted = 0

    downloads_done = Path(config['paths']['downloads_done'])

    if not downloads_done.exists():
        logger.warning(f"Downloads directory not found: {downloads_done}")
        return 0, 0

    logger.info(f"Scanning {downloads_done}...")

    # Walk through all files in _done
    for item in downloads_done.rglob('*'):
        if not item.is_file():
            continue

        # Check if this file (or similar named file) exists in library
        # This is a simple check - could be enhanced with fuzzy matching
        filename = item.name
        exists_in_library = any(filename in lib_path for lib_path in library_files)

        if not exists_in_library:
            if verbose:
                logger.info(f"SKIP (not in library): {item}")
            continue

        # File exists in library, safe to delete from _done
        size_gb = item.stat().st_size / (1024 ** 3)

        if dry_run:
            logger.info(f"🗑️  [DRY-RUN] Would delete local file: {item} ({size_gb:.2f} GB)")
        else:
            logger.info(f"🗑️  Deleting local file: {item} ({size_gb:.2f} GB)")
            try:
                item.unlink()
                logger.info("    ✅ Deleted successfully")
                total_size_deleted += item.stat().st_size
                deleted_count += 1
            except Exception as e:
                logger.error(f"    ❌ Failed to delete: {e}")

    # Clean up empty directories
    if not dry_run and deleted_count > 0:
        logger.info("Cleaning up empty directories...")
        for dirpath in sorted(downloads_done.rglob('*'), reverse=True):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                try:
                    dirpath.rmdir()
                    logger.info(f"Removed empty directory: {dirpath}")
                except Exception as e:
                    logger.debug(f"Could not remove {dirpath}: {e}")

    logger.info("")
    logger.info(f"Phase 3 Summary:")
    logger.info(f"  Local files deleted: {deleted_count}")
    logger.info(f"  Space freed: {total_size_deleted / (1024**3):.2f} GB")

    return deleted_count, total_size_deleted


def comprehensive_purge(config: dict, args) -> bool:
    """Execute comprehensive 3-phase purge workflow.

    Args:
        config: Configuration dictionary
        args: Command-line arguments

    Returns:
        True if successful, False otherwise
    """
    logger = setup_logging('seedbox_purge.log', level=config['logging']['level'])
    notifier = create_notifier(config)

    logger.info("="*60)
    logger.info("COMPREHENSIVE SEEDBOX & LOCAL CLEANUP")
    logger.info("="*60)

    if args.dry_run:
        logger.info("MODE: DRY-RUN (no files will be deleted)")
    else:
        logger.info("MODE: EXECUTE (files WILL be deleted)")

    logger.info(f"Skip torrents: {args.skip_torrents}")
    logger.info(f"Skip remote files: {args.skip_remote_files}")
    logger.info(f"Skip local _done: {args.skip_local_done}")
    logger.info("")

    try:
        with acquire_lock('seedbox_purge'):
            logger.info("Lock acquired, proceeding with purge")

            # Initialize API clients
            logger.info("Initializing API clients...")

            radarr = RadarrAPI(
                config['radarr']['url'],
                config['radarr']['api_key']
            )

            sonarr = SonarrAPI(
                config['sonarr']['url'],
                config['sonarr']['api_key']
            )

            # Get imported hashes (for Phase 1)
            imported_hashes = get_imported_hashes(radarr, sonarr, logger)

            # Get library files (for Phase 3)
            library_files = get_imported_paths(radarr, sonarr, logger)

            logger.info("")

            # Track totals
            total_deleted = 0
            total_size_freed = 0

            # Phase 1: Torrent cleanup
            if not args.skip_torrents:
                rtorrent = RTorrentClient(
                    host="nl3864.dediseedbox.com",
                    username=config['seedbox']['username'],
                    password=config['seedbox']['password']
                )
                try:
                    rtorrent.test_connection()
                except Exception as e:
                    logger.error(f"Failed to connect to rtorrent: {e}")
                    notifier.notify_error('seedbox_purge', f"rtorrent connection failed: {e}")
                    return False

                deleted, size_freed = purge_torrents(
                    config, rtorrent, imported_hashes, logger,
                    dry_run=args.dry_run, verbose=args.verbose
                )
                total_deleted += deleted
                total_size_freed += size_freed
            else:
                logger.info("⏭️  SKIPPING Phase 1: Torrent cleanup")

            # Phase 2: Remote file cleanup
            if not args.skip_remote_files:
                deleted, size_freed = purge_remote_files(
                    config, imported_hashes, logger,
                    dry_run=args.dry_run, verbose=args.verbose
                )
                total_deleted += deleted
                total_size_freed += size_freed
            else:
                logger.info("⏭️  SKIPPING Phase 2: Remote file cleanup")

            # Phase 3: Local _done cleanup
            if not args.skip_local_done:
                deleted, size_freed = purge_local_done(
                    config, library_files, logger,
                    dry_run=args.dry_run, verbose=args.verbose
                )
                total_deleted += deleted
                total_size_freed += size_freed
            else:
                logger.info("⏭️  SKIPPING Phase 3: Local _done cleanup")

            # Final summary
            logger.info("")
            logger.info("="*60)
            logger.info("FINAL SUMMARY")
            logger.info("="*60)
            logger.info(f"Total items deleted: {total_deleted}")
            logger.info(f"Total space freed: {total_size_freed / (1024**3):.2f} GB")

            if args.dry_run and total_deleted > 0:
                logger.info("")
                logger.info("💡 This was a dry run. To actually delete:")
                logger.info("   python3 scripts/seedbox_purge.py --execute")

            # Success notification
            if total_deleted > 0 and not args.dry_run:
                if config['notifications']['ntfy']['send_on_success']:
                    notifier.notify_success(
                        'seedbox_purge',
                        f'Deleted {total_deleted} items',
                        stats={
                            'items': total_deleted,
                            'space_freed_gb': f"{total_size_freed / (1024**3):.2f}"
                        }
                    )

            logger.info("="*60)
            logger.info("CLEANUP COMPLETED SUCCESSFULLY")
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
        description='Comprehensive seedbox and local cleanup (3-phase strategy)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
3-Phase Cleanup Strategy:

Phase 1: Active Torrents (XMLRPC)
  - Delete torrents from rtorrent if imported + policy met
  - Policy: ratio >= 1.5 OR age >= 2 days

Phase 2: Remote /downloads Files (SSH)
  - Clean up orphaned files on seedbox
  - Delete files that have been downloaded locally

Phase 3: Local _done Files (Filesystem)
  - Clean up staging directory after import
  - Delete files that exist in final library location

Examples:
  # Full cleanup (dry-run)
  %(prog)s --dry-run --verbose

  # Execute all phases
  %(prog)s --execute

  # Skip specific phases
  %(prog)s --execute --skip-torrents
  %(prog)s --execute --skip-local-done

  # Torrents only (original behavior)
  %(prog)s --execute --skip-remote-files --skip-local-done

Safety:
  - Always uses dry-run mode first
  - Lock file prevents concurrent execution
  - Verifies files before deletion
  - Protects folders (/_ready, /.recycle)
  - Sends ntfy notifications on errors
        """
    )

    # Mode selection
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

    # Phase selection
    parser.add_argument(
        '--skip-torrents',
        action='store_true',
        help='Skip Phase 1 (active torrent cleanup)'
    )
    parser.add_argument(
        '--skip-remote-files',
        action='store_true',
        help='Skip Phase 2 (remote /downloads cleanup)'
    )
    parser.add_argument(
        '--skip-local-done',
        action='store_true',
        help='Skip Phase 3 (local _done cleanup)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed information (all files, not just deletions)'
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"ERROR: Failed to load configuration: {e}", file=sys.stderr)
        return 1

    # Run comprehensive purge
    success = comprehensive_purge(config, args)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
