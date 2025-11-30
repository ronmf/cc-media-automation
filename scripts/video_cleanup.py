#!/usr/bin/env python3
"""Video cleanup script - remove extras, trailers, and metadata files.

This script scans media folders and removes extra files like trailers,
featurettes, samples, and metadata files (NFO, fanart, posters).
It keeps main video files and subtitle files.

Features:
- Scan multiple media folders (movies, series, kids_movies, kids_series)
- Identify extras by patterns (trailer, sample, featurette, etc.)
- For MOVIES: Also delete small videos (< min_size_mb) as likely samples
- For SERIES: Keep all episodes regardless of size (no size-based deletion)
- Direct filesystem deletion for extras (not tracked by Radarr/Sonarr)
- Remove metadata files (NFO, fanart, posters) - Jellyfin retrieves these
- Keep subtitle files (.srt, .ass, .sub)
- Comprehensive logging and error handling
- ntfy notifications on errors

Usage:
    # Dry-run mode (default - shows what would be deleted)
    python3 scripts/video_cleanup.py --dry-run

    # Execute mode (actually delete files)
    python3 scripts/video_cleanup.py --execute

    # Clean specific folder only
    python3 scripts/video_cleanup.py --execute --folder movies

    # Custom config file
    python3 scripts/video_cleanup.py --execute --config /path/to/config.yaml
"""

import sys
import argparse
import os
from pathlib import Path
from typing import List, Dict

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import (
    acquire_lock,
    get_video_files,
    find_main_video,
    is_extra_file,
    is_subtitle_file,
    is_metadata_file
)


def cleanup_folder(
    folder_path: str,
    folder_name: str,
    config: dict,
    logger,
    dry_run: bool = False,
    is_series: bool = False
) -> Dict[str, int]:
    """Clean up extras and metadata in a media folder.

    Args:
        folder_path: Path to folder to clean
        folder_name: Name of folder (for logging)
        config: Configuration dictionary
        logger: Logger instance
        dry_run: If True, don't actually delete files
        is_series: If True, don't delete small files (series have many episodes)

    Returns:
        Dictionary with stats (extras_deleted, metadata_deleted, space_freed_mb)
    """
    stats = {
        'extras_deleted': 0,
        'metadata_deleted': 0,
        'space_freed_mb': 0
    }

    if not os.path.isdir(folder_path):
        logger.warning(f"Folder does not exist: {folder_path}")
        return stats

    logger.info(f"\nProcessing folder: {folder_name}")
    logger.info("-" * 60)

    min_size_mb = config['thresholds']['min_video_size_mb']
    extra_patterns = config['thresholds']['extra_patterns']

    # Walk through all subdirectories (each movie/series has its own folder)
    for root, dirs, files in os.walk(folder_path):
        # Get all video files in this directory
        video_files = [
            os.path.join(root, f) for f in files
            if os.path.join(root, f) in get_video_files(root)
        ]

        if not video_files:
            continue

        # Find main video file
        main_video = find_main_video(video_files, min_size_mb)

        if not main_video:
            logger.debug(f"No main video found in {root}")
            continue

        main_video_name = os.path.basename(main_video)
        logger.debug(f"Main video: {main_video_name}")

        # Check for extra video files
        for video_file in video_files:
            if video_file == main_video:
                continue

            video_name = os.path.basename(video_file)

            # Check if it's an extra by pattern
            if is_extra_file(video_name, extra_patterns):
                file_size = os.path.getsize(video_file)
                size_mb = file_size / (1024 * 1024)

                if dry_run:
                    logger.info(f"[DRY-RUN] Would delete extra: {video_name} ({size_mb:.1f} MB)")
                else:
                    try:
                        os.remove(video_file)
                        logger.info(f"DELETED extra: {video_name} ({size_mb:.1f} MB)")
                        stats['extras_deleted'] += 1
                        stats['space_freed_mb'] += size_mb
                    except OSError as e:
                        logger.error(f"Failed to delete {video_name}: {e}")

            # Check if it's small (likely extra/sample) - ONLY for movies
            # For series, all episodes are legitimate, don't delete by size
            elif not is_series:
                file_size = os.path.getsize(video_file)
                size_mb = file_size / (1024 * 1024)

                if size_mb < min_size_mb:
                    if dry_run:
                        logger.info(f"[DRY-RUN] Would delete small video: {video_name} ({size_mb:.1f} MB)")
                    else:
                        try:
                            os.remove(video_file)
                            logger.info(f"DELETED small video: {video_name} ({size_mb:.1f} MB)")
                            stats['extras_deleted'] += 1
                            stats['space_freed_mb'] += size_mb
                        except OSError as e:
                            logger.error(f"Failed to delete {video_name}: {e}")

        # Clean up metadata files in this directory
        for filename in files:
            filepath = os.path.join(root, filename)

            # Skip if it's a video file (already handled)
            if filepath in video_files:
                continue

            # Keep subtitle files
            if is_subtitle_file(filename):
                logger.debug(f"KEEP subtitle: {filename}")
                continue

            # Remove metadata files
            if is_metadata_file(filename):
                file_size = os.path.getsize(filepath)
                size_mb = file_size / (1024 * 1024)

                if dry_run:
                    logger.info(f"[DRY-RUN] Would delete metadata: {filename} ({size_mb:.2f} MB)")
                else:
                    try:
                        os.remove(filepath)
                        logger.info(f"DELETED metadata: {filename} ({size_mb:.2f} MB)")
                        stats['metadata_deleted'] += 1
                        stats['space_freed_mb'] += size_mb
                    except OSError as e:
                        logger.error(f"Failed to delete {filename}: {e}")

    return stats


def cleanup_videos(config: dict, dry_run: bool = False, specific_folder: str = None) -> bool:
    """Clean up extra files from media folders.

    Args:
        config: Configuration dictionary
        dry_run: If True, don't actually delete files
        specific_folder: If specified, only clean this folder

    Returns:
        True if cleanup successful, False otherwise
    """
    logger = setup_logging('video_cleanup.log', level=config['logging']['level'])
    notifier = create_notifier(config)

    logger.info("="*60)
    logger.info("VIDEO CLEANUP STARTED")
    logger.info("="*60)

    if dry_run:
        logger.info("DRY-RUN MODE: Files will NOT be deleted")
    else:
        logger.info("EXECUTE MODE: Files WILL be deleted")

    try:
        # Acquire lock
        with acquire_lock('video_cleanup'):
            logger.info("Lock acquired, proceeding with cleanup")

            # Define folders to clean
            folders = {
                'movies': config['paths']['movies'],
                'series': config['paths']['series'],
                'kids_movies': config['paths']['kids_movies'],
                'kids_series': config['paths']['kids_series']
            }

            # Filter to specific folder if requested
            if specific_folder:
                if specific_folder not in folders:
                    logger.error(f"Unknown folder: {specific_folder}")
                    logger.error(f"Available folders: {', '.join(folders.keys())}")
                    return False
                folders = {specific_folder: folders[specific_folder]}

            # Process each folder
            total_stats = {
                'extras_deleted': 0,
                'metadata_deleted': 0,
                'space_freed_mb': 0
            }

            for folder_name, folder_path in folders.items():
                # Determine if this is a series folder
                is_series_folder = 'series' in folder_name.lower()

                stats = cleanup_folder(
                    folder_path,
                    folder_name,
                    config,
                    logger,
                    dry_run,
                    is_series=is_series_folder
                )

                # Accumulate stats
                for key in total_stats:
                    total_stats[key] += stats[key]

            # Summary
            logger.info("\n" + "="*60)
            logger.info("CLEANUP SUMMARY")
            logger.info("="*60)
            logger.info(f"Extras deleted: {total_stats['extras_deleted']}")
            logger.info(f"Metadata deleted: {total_stats['metadata_deleted']}")
            logger.info(f"Total files deleted: {total_stats['extras_deleted'] + total_stats['metadata_deleted']}")
            logger.info(f"Space freed: {total_stats['space_freed_mb']:.2f} MB ({total_stats['space_freed_mb']/1024:.2f} GB)")

            # Success notification (if files were deleted)
            total_deleted = total_stats['extras_deleted'] + total_stats['metadata_deleted']
            if total_deleted > 0 and config['notifications']['ntfy']['send_on_success']:
                notifier.notify_success(
                    'video_cleanup',
                    f'Cleaned up {total_deleted} files',
                    stats={
                        'extras': total_stats['extras_deleted'],
                        'metadata': total_stats['metadata_deleted'],
                        'space_freed_gb': f"{total_stats['space_freed_mb']/1024:.2f}",
                        'mode': 'DRY-RUN' if dry_run else 'EXECUTE'
                    }
                )

            logger.info("="*60)
            logger.info("VIDEO CLEANUP COMPLETED SUCCESSFULLY")
            logger.info("="*60)

            return True

    except Exception as e:
        error_msg = f"Unexpected error during cleanup: {e}"
        logger.exception(error_msg)
        notifier.notify_error('video_cleanup', error_msg, details=str(e))
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Clean up extra files from media folders',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (default - safe mode, shows what would be deleted)
  %(prog)s --dry-run

  # Execute (actually delete files)
  %(prog)s --execute

  # Clean specific folder only
  %(prog)s --execute --folder movies

  # Use custom config file
  %(prog)s --execute --config /path/to/config.yaml

Notes:
  - Pattern-based deletion: trailers, featurettes, samples, etc. (all folders)
  - Size-based deletion: small videos < 300 MB (MOVIES ONLY, not series)
  - Series episodes are kept regardless of size (handles anime, sitcoms)
  - Deletes metadata: NFO files, fanart, posters (Jellyfin retrieves these)
  - Keeps subtitle files (.srt, .ass, .sub)
  - Lock file prevents concurrent execution
  - Logs to logs/video_cleanup.log
  - Sends ntfy notifications on errors

Available folders: movies, series, kids_movies, kids_series
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

    parser.add_argument(
        '--folder',
        type=str,
        choices=['movies', 'series', 'kids_movies', 'kids_series'],
        help='Clean only this specific folder (default: all folders)'
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

    # Run cleanup
    success = cleanup_videos(config, dry_run=dry_run, specific_folder=args.folder)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
