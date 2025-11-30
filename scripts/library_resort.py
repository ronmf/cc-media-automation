#!/usr/bin/env python3
"""Library resort script - move content between kids and adult libraries.

This script analyzes movies and series in Radarr/Sonarr and moves them to
the correct library (kids vs adult) based on their age ratings.

Features:
- Check age ratings from Radarr/Sonarr metadata
- Move misplaced movies between movies <-> kids_movies
- Move misplaced series between series <-> kids_series
- Update root folder paths in Radarr/Sonarr
- Optionally trigger file moves/renames
- Comprehensive logging and reporting
- ntfy notifications

Usage:
    # Dry-run mode (default - shows what would be moved)
    python3 scripts/library_resort.py --dry-run

    # Execute mode (actually move content)
    python3 scripts/library_resort.py --execute

    # Movies only
    python3 scripts/library_resort.py --execute --movies-only

    # Series only
    python3 scripts/library_resort.py --execute --series-only

    # Custom config file
    python3 scripts/library_resort.py --execute --config /path/to/config.yaml
"""

import sys
import argparse
from pathlib import Path
from typing import List, Dict, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import acquire_lock
from utils.api_clients import RadarrAPI, SonarrAPI


def is_kids_rating(certification: str, kids_ratings: List[str]) -> bool:
    """Check if a certification is considered kids content.

    Args:
        certification: Age rating (e.g., 'G', 'PG', 'R', 'TV-Y7')
        kids_ratings: List of certifications considered safe for kids

    Returns:
        True if certification is in kids list
    """
    if not certification:
        return False

    return certification.upper() in [r.upper() for r in kids_ratings]


def resort_movies(
    config: dict,
    logger,
    dry_run: bool = False
) -> Tuple[int, int]:
    """Resort movies between movies and kids_movies libraries.

    Args:
        config: Configuration dictionary
        logger: Logger instance
        dry_run: If True, don't actually move items

    Returns:
        Tuple of (to_kids_count, to_adult_count)
    """
    logger.info("\n" + "="*60)
    logger.info("RESORTING MOVIES")
    logger.info("="*60)

    radarr = RadarrAPI(
        config['radarr']['url'],
        config['radarr']['api_key']
    )

    kids_ratings = config['thresholds']['kids_age_ratings']['movies']
    movies_path = config['paths']['movies']
    kids_movies_path = config['paths']['kids_movies']

    # Get all movies
    all_movies = radarr.get_movies()
    logger.info(f"Found {len(all_movies)} total movies")

    to_kids = []      # Adult movies that should be in kids
    to_adult = []     # Kids movies that should be in adult

    # Analyze each movie
    for movie in all_movies:
        title = movie.get('title', 'Unknown')
        year = movie.get('year', 0)
        movie_id = movie.get('id')
        current_path = movie.get('path', '')
        certification = movie.get('certification', '')

        # Determine current library
        in_kids = kids_movies_path in current_path
        in_adult = movies_path in current_path

        # Skip if not in either library (shouldn't happen)
        if not in_kids and not in_adult:
            logger.warning(f"SKIP: {title} ({year}) - not in known library: {current_path}")
            continue

        # Check if rating matches library
        should_be_kids = is_kids_rating(certification, kids_ratings)

        # Misplaced in adult library?
        if in_adult and should_be_kids:
            to_kids.append({
                'id': movie_id,
                'title': title,
                'year': year,
                'certification': certification,
                'current_path': current_path
            })

        # Misplaced in kids library?
        elif in_kids and not should_be_kids:
            to_adult.append({
                'id': movie_id,
                'title': title,
                'year': year,
                'certification': certification or 'UNRATED',
                'current_path': current_path
            })

    # Report findings
    logger.info(f"\nMovies to move to KIDS library: {len(to_kids)}")
    for item in to_kids:
        logger.info(f"  → {item['title']} ({item['year']}) [{item['certification']}]")

    logger.info(f"\nMovies to move to ADULT library: {len(to_adult)}")
    for item in to_adult:
        logger.info(f"  → {item['title']} ({item['year']}) [{item['certification']}]")

    # Execute moves
    if not dry_run:
        logger.info("\nExecuting moves...")

        # Move to kids
        for item in to_kids:
            new_path = item['current_path'].replace(movies_path, kids_movies_path)
            try:
                radarr.update_movie(
                    item['id'],
                    {'path': new_path, 'rootFolderPath': kids_movies_path}
                )
                logger.info(f"MOVED to kids: {item['title']} ({item['year']})")
            except Exception as e:
                logger.error(f"Failed to move {item['title']}: {e}")

        # Move to adult
        for item in to_adult:
            new_path = item['current_path'].replace(kids_movies_path, movies_path)
            try:
                radarr.update_movie(
                    item['id'],
                    {'path': new_path, 'rootFolderPath': movies_path}
                )
                logger.info(f"MOVED to adult: {item['title']} ({item['year']})")
            except Exception as e:
                logger.error(f"Failed to move {item['title']}: {e}")

    else:
        logger.info("\n[DRY-RUN] No changes made")

    return len(to_kids), len(to_adult)


def resort_series(
    config: dict,
    logger,
    dry_run: bool = False
) -> Tuple[int, int]:
    """Resort series between series and kids_series libraries.

    Args:
        config: Configuration dictionary
        logger: Logger instance
        dry_run: If True, don't actually move items

    Returns:
        Tuple of (to_kids_count, to_adult_count)
    """
    logger.info("\n" + "="*60)
    logger.info("RESORTING SERIES")
    logger.info("="*60)

    sonarr = SonarrAPI(
        config['sonarr']['url'],
        config['sonarr']['api_key']
    )

    kids_ratings = config['thresholds']['kids_age_ratings']['series']
    series_path = config['paths']['series']
    kids_series_path = config['paths']['kids_series']

    # Get all series
    all_series = sonarr.get_series()
    logger.info(f"Found {len(all_series)} total series")

    to_kids = []      # Adult series that should be in kids
    to_adult = []     # Kids series that should be in adult

    # Analyze each series
    for show in all_series:
        title = show.get('title', 'Unknown')
        year = show.get('year', 0)
        series_id = show.get('id')
        current_path = show.get('path', '')
        certification = show.get('certification', '')

        # Determine current library
        in_kids = kids_series_path in current_path
        in_adult = series_path in current_path

        # Skip if not in either library
        if not in_kids and not in_adult:
            logger.warning(f"SKIP: {title} ({year}) - not in known library: {current_path}")
            continue

        # Check if rating matches library
        should_be_kids = is_kids_rating(certification, kids_ratings)

        # Misplaced in adult library?
        if in_adult and should_be_kids:
            to_kids.append({
                'id': series_id,
                'title': title,
                'year': year,
                'certification': certification,
                'current_path': current_path
            })

        # Misplaced in kids library?
        elif in_kids and not should_be_kids:
            to_adult.append({
                'id': series_id,
                'title': title,
                'year': year,
                'certification': certification or 'UNRATED',
                'current_path': current_path
            })

    # Report findings
    logger.info(f"\nSeries to move to KIDS library: {len(to_kids)}")
    for item in to_kids:
        logger.info(f"  → {item['title']} ({item['year']}) [{item['certification']}]")

    logger.info(f"\nSeries to move to ADULT library: {len(to_adult)}")
    for item in to_adult:
        logger.info(f"  → {item['title']} ({item['year']}) [{item['certification']}]")

    # Execute moves
    if not dry_run:
        logger.info("\nExecuting moves...")

        # Move to kids
        for item in to_kids:
            new_path = item['current_path'].replace(series_path, kids_series_path)
            try:
                sonarr.update_series(
                    item['id'],
                    {'path': new_path, 'rootFolderPath': kids_series_path}
                )
                logger.info(f"MOVED to kids: {item['title']} ({item['year']})")
            except Exception as e:
                logger.error(f"Failed to move {item['title']}: {e}")

        # Move to adult
        for item in to_adult:
            new_path = item['current_path'].replace(kids_series_path, series_path)
            try:
                sonarr.update_series(
                    item['id'],
                    {'path': new_path, 'rootFolderPath': series_path}
                )
                logger.info(f"MOVED to adult: {item['title']} ({item['year']})")
            except Exception as e:
                logger.error(f"Failed to move {item['title']}: {e}")

    else:
        logger.info("\n[DRY-RUN] No changes made")

    return len(to_kids), len(to_adult)


def resort_libraries(
    config: dict,
    dry_run: bool = False,
    movies_only: bool = False,
    series_only: bool = False
) -> bool:
    """Resort content between kids and adult libraries.

    Args:
        config: Configuration dictionary
        dry_run: If True, don't actually move items
        movies_only: If True, only process movies
        series_only: If True, only process series

    Returns:
        True if successful, False otherwise
    """
    logger = setup_logging('library_resort.log', level=config['logging']['level'])
    notifier = create_notifier(config)

    logger.info("="*60)
    logger.info("LIBRARY RESORT STARTED")
    logger.info("="*60)

    if dry_run:
        logger.info("DRY-RUN MODE: Items will NOT be moved")
    else:
        logger.info("EXECUTE MODE: Items WILL be moved")

    try:
        # Acquire lock
        with acquire_lock('library_resort'):
            logger.info("Lock acquired, proceeding with resort")

            total_stats = {
                'movies_to_kids': 0,
                'movies_to_adult': 0,
                'series_to_kids': 0,
                'series_to_adult': 0
            }

            # Process movies
            if not series_only:
                try:
                    movies_to_kids, movies_to_adult = resort_movies(config, logger, dry_run)
                    total_stats['movies_to_kids'] = movies_to_kids
                    total_stats['movies_to_adult'] = movies_to_adult
                except Exception as e:
                    logger.error(f"Failed to resort movies: {e}")
                    logger.exception(e)

            # Process series
            if not movies_only:
                try:
                    series_to_kids, series_to_adult = resort_series(config, logger, dry_run)
                    total_stats['series_to_kids'] = series_to_kids
                    total_stats['series_to_adult'] = series_to_adult
                except Exception as e:
                    logger.error(f"Failed to resort series: {e}")
                    logger.exception(e)

            # Summary
            logger.info("\n" + "="*60)
            logger.info("RESORT SUMMARY")
            logger.info("="*60)
            logger.info(f"Movies moved to kids: {total_stats['movies_to_kids']}")
            logger.info(f"Movies moved to adult: {total_stats['movies_to_adult']}")
            logger.info(f"Series moved to kids: {total_stats['series_to_kids']}")
            logger.info(f"Series moved to adult: {total_stats['series_to_adult']}")

            total_moved = sum(total_stats.values())
            logger.info(f"Total items moved: {total_moved}")

            # Success notification (if items were moved)
            if total_moved > 0 and config['notifications']['ntfy']['send_on_success']:
                notifier.notify_success(
                    'library_resort',
                    f'Resorted {total_moved} items',
                    stats=total_stats
                )

            logger.info("="*60)
            logger.info("LIBRARY RESORT COMPLETED SUCCESSFULLY")
            logger.info("="*60)

            return True

    except Exception as e:
        error_msg = f"Unexpected error during resort: {e}"
        logger.exception(error_msg)
        notifier.notify_error('library_resort', error_msg, details=str(e))
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Resort content between kids and adult libraries',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (default - safe mode, shows what would be moved)
  %(prog)s --dry-run

  # Execute (actually move items)
  %(prog)s --execute

  # Movies only
  %(prog)s --execute --movies-only

  # Series only
  %(prog)s --execute --series-only

  # Use custom config file
  %(prog)s --execute --config /path/to/config.yaml

Notes:
  - Checks age ratings from Radarr/Sonarr metadata
  - Moves items with kids ratings (G, PG, TV-Y, TV-PG) to kids libraries
  - Moves items with adult ratings (R, TV-14, TV-MA, etc.) to adult libraries
  - Unrated content defaults to adult library
  - Updates root folder paths in Radarr/Sonarr
  - Lock file prevents concurrent execution
  - Logs to logs/library_resort.log
  - Sends ntfy notifications on completion
        """
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry-run mode: show what would be moved (safe)'
    )
    mode_group.add_argument(
        '--execute',
        action='store_true',
        help='Execute mode: actually move items'
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )

    # Content type filters
    content_group = parser.add_mutually_exclusive_group()
    content_group.add_argument(
        '--movies-only',
        action='store_true',
        help='Only process movies (skip series)'
    )
    content_group.add_argument(
        '--series-only',
        action='store_true',
        help='Only process series (skip movies)'
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

    # Run resort
    success = resort_libraries(
        config,
        dry_run=dry_run,
        movies_only=args.movies_only,
        series_only=args.series_only
    )

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
