#!/usr/bin/env python3
"""Library analyzer - analyze watch patterns and calculate deletion scores.

This script analyzes your media library by combining data from Jellyfin
(watch history), Radarr/Sonarr (quality, ratings, file info), and optionally
Prowlarr (availability for re-acquisition). It calculates a deletion score
(0-100) for each item, with higher scores indicating better candidates for removal.

Scoring Algorithm:
  - Watch history (35%): Never watched or watched long ago
  - Quality (25%): Lower quality (720p, 480p) scores higher
  - Ratings (20%): Low IMDB/TMDB ratings score higher
  - Duplicates (10%): If better quality version exists
  - Context (5%): Age, library type
  - Size (5%): Large files that are unwatched

Protected Content (never suggested):
  - Recently added (< 30 days)
  - In Jellyfin collections/favorites
  - Specific tags (favorite, keep, protected)
  - High quality (1080p+) by default

Usage:
    # Analyze library and generate report
    python3 scripts/library_analyzer.py --dry-run

    # Execute (same as dry-run, no destructive operations)
    python3 scripts/library_analyzer.py --execute

    # Analyze specific type only
    python3 scripts/library_analyzer.py --execute --type movies

    # Custom output file
    python3 scripts/library_analyzer.py --execute --output /path/to/report.csv
"""

import sys
import argparse
import os
import csv
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import acquire_lock
from utils.api_clients import RadarrAPI, SonarrAPI, JellyfinAPI, ProwlarrAPI, APIError


def parse_quality(quality_str: str) -> str:
    """Extract resolution from quality string.

    Args:
        quality_str: Quality string from Radarr/Sonarr

    Returns:
        Resolution (2160p, 1080p, 720p, 480p, or unknown)
    """
    quality_lower = quality_str.lower()

    if '2160' in quality_lower or '4k' in quality_lower or 'uhd' in quality_lower:
        return '2160p'
    elif '1080' in quality_lower or 'bluray-1080' in quality_lower:
        return '1080p'
    elif '720' in quality_lower:
        return '720p'
    elif '480' in quality_lower or 'dvd' in quality_lower:
        return '480p'
    else:
        return 'unknown'


def calculate_deletion_score(item: Dict[str, Any], config: Dict) -> tuple[int, str]:
    """Calculate deletion score for an item (0-100).

    Higher score = better candidate for deletion.

    Args:
        item: Item dictionary with all metadata
        config: Configuration dictionary

    Returns:
        Tuple of (score, reason_summary)
    """
    score = 0.0
    reasons = []

    criteria = config['analyzer']['criteria']

    # CRITERION 1: Watch History (35% weight)
    watch_weight = criteria['watch_history_weight']
    never_watched = not item.get('played', False)
    last_played = item.get('last_played_date')

    if never_watched:
        age_months = item.get('age_months', 0)
        # Max points after 1 year
        watch_score = min(35 * (age_months / 12), 35)
        score += watch_score
        reasons.append(f"never watched ({age_months}mo old)")
    elif last_played:
        days_since = (datetime.now() - last_played).days
        if days_since > 730:  # 2 years
            score += 20
            reasons.append(f"last watched {days_since}d ago")
        elif days_since > 365:  # 1 year
            score += 10
            reasons.append(f"last watched {days_since}d ago")

    # CRITERION 2: Quality (25% weight)
    quality = item.get('quality', 'unknown')
    quality_weights = config['thresholds']['quality_weights']

    if quality == '720p':
        score += 15
        reasons.append("720p quality")
    elif quality in ['480p', 'unknown']:
        score += 25
        reasons.append(f"{quality} quality")

    # Bonus for low bitrate
    if item.get('bitrate_kbps', 0) > 0 and item['bitrate_kbps'] < 2000:
        score += 10
        reasons.append("low bitrate")

    # CRITERION 3: Ratings (20% weight)
    rating = item.get('rating', 0.0)
    vote_count = item.get('vote_count', 0)

    if rating < 5.0:
        score += 20
        reasons.append(f"low rating ({rating:.1f})")
    elif rating < 6.5:
        score += 10
        reasons.append(f"mediocre rating ({rating:.1f})")

    if vote_count < 100:
        score += 5
        reasons.append("unreliable rating")

    # CRITERION 4: Duplicates (10% weight)
    if item.get('has_better_version', False):
        score += 10
        reasons.append("better version exists")

    # CRITERION 5: Context (5% weight)
    # Currently simple, can be extended
    if item.get('in_kids_library', False):
        # Could check child age threshold here
        pass

    # CRITERION 6: File Size (5% weight)
    size_gb = item.get('size_gb', 0)
    if size_gb > 20 and never_watched:
        score += 5
        reasons.append("large unwatched file")

    # Protection adjustments
    protected = config['analyzer']['protected']

    # Recently added (< 30 days) - reduce score
    if item.get('age_days', 999) < protected['recently_added_days']:
        score *= 0.5
        reasons.append("recently added (protected)")

    # High quality protection (1080p+)
    if quality in ['2160p', '1080p']:
        protection_factor = quality_weights.get(quality, 1.0)
        score *= protection_factor
        if protection_factor < 1.0:
            reasons.append(f"{quality} protected")

    # Protected tags
    item_tags = item.get('tags', [])
    if any(tag in protected['tags'] for tag in item_tags):
        score = 0
        reasons = ["protected by tag"]

    # Cap at 100
    final_score = min(int(score), 100)
    reason_summary = "; ".join(reasons[:3])  # Top 3 reasons

    return final_score, reason_summary


def analyze_movies(config: Dict, logger) -> List[Dict[str, Any]]:
    """Analyze all movies and calculate deletion scores.

    Args:
        config: Configuration dictionary
        logger: Logger instance

    Returns:
        List of movie dictionaries with scores
    """
    logger.info("Analyzing movies...")

    results = []

    try:
        # Get Radarr movies
        radarr = RadarrAPI(config['radarr']['url'], config['radarr']['api_key'])
        movies = radarr.get_movies()
        logger.info(f"Found {len(movies)} movies in Radarr")

        # Get Jellyfin items (basic info only, no user ID needed)
        try:
            jellyfin = JellyfinAPI(config['jellyfin']['url'], config['jellyfin']['api_key'])
            jellyfin_items = jellyfin.get_items(include_item_types='Movie')
            jellyfin_dict = {item['Path']: item for item in jellyfin_items}
        except APIError as e:
            logger.warning(f"Could not get Jellyfin data: {e}")
            jellyfin_dict = {}

        # Process each movie
        for movie in movies:
            # Basic info
            item = {
                'title': movie['title'],
                'type': 'movie',
                'year': movie.get('year', ''),
                'path': movie.get('path', ''),
                'radarr_id': movie['id'],
                'tags': [t for t in movie.get('tags', [])],
            }

            # Quality
            quality_profile = movie.get('qualityProfileId')
            if movie.get('movieFile'):
                quality_str = movie['movieFile'].get('quality', {}).get('quality', {}).get('name', 'unknown')
                item['quality'] = parse_quality(quality_str)

                # File size
                size_bytes = movie['movieFile'].get('size', 0)
                item['size_gb'] = size_bytes / (1024**3)

                # Bitrate (approximate)
                runtime_minutes = movie.get('runtime', 0)
                if runtime_minutes > 0:
                    bitrate_kbps = (size_bytes * 8) / (runtime_minutes * 60 * 1000)
                    item['bitrate_kbps'] = bitrate_kbps
            else:
                item['quality'] = 'unknown'
                item['size_gb'] = 0

            # Ratings
            ratings = movie.get('ratings', {})
            if 'imdb' in ratings:
                item['rating'] = ratings['imdb'].get('value', 0.0)
                item['vote_count'] = ratings['imdb'].get('votes', 0)
            elif 'tmdb' in ratings:
                item['rating'] = ratings['tmdb'].get('value', 0.0)
                item['vote_count'] = ratings['tmdb'].get('votes', 0)
            else:
                item['rating'] = 0.0
                item['vote_count'] = 0

            # Age
            added_date = movie.get('added')
            if added_date:
                added_dt = datetime.fromisoformat(added_date.replace('Z', '+00:00'))
                age_days = (datetime.now() - added_dt.replace(tzinfo=None)).days
                item['age_days'] = age_days
                item['age_months'] = age_days / 30
            else:
                item['age_days'] = 999
                item['age_months'] = 99

            # Watch status (from Jellyfin if available)
            jellyfin_item = jellyfin_dict.get(movie.get('path', ''))
            if jellyfin_item:
                item['played'] = jellyfin_item.get('UserData', {}).get('Played', False)
                last_played = jellyfin_item.get('UserData', {}).get('LastPlayedDate')
                if last_played:
                    item['last_played_date'] = datetime.fromisoformat(last_played.replace('Z', '+00:00')).replace(tzinfo=None)
            else:
                item['played'] = False
                item['last_played_date'] = None

            # Library context
            item['in_kids_library'] = 'kids' in movie.get('path', '').lower()

            # Calculate score
            score, reason = calculate_deletion_score(item, config)
            item['score'] = score
            item['reason'] = reason

            results.append(item)

    except APIError as e:
        logger.error(f"API error while analyzing movies: {e}")

    return results


def analyze_series(config: Dict, logger) -> List[Dict[str, Any]]:
    """Analyze all series and calculate deletion scores.

    Args:
        config: Configuration dictionary
        logger: Logger instance

    Returns:
        List of series dictionaries with scores
    """
    logger.info("Analyzing series...")

    results = []

    try:
        # Get Sonarr series
        sonarr = SonarrAPI(config['sonarr']['url'], config['sonarr']['api_key'])
        all_series = sonarr.get_series()
        logger.info(f"Found {len(all_series)} series in Sonarr")

        # Get Jellyfin items
        try:
            jellyfin = JellyfinAPI(config['jellyfin']['url'], config['jellyfin']['api_key'])
            jellyfin_items = jellyfin.get_items(include_item_types='Series')
            jellyfin_dict = {item['Path']: item for item in jellyfin_items}
        except APIError as e:
            logger.warning(f"Could not get Jellyfin data: {e}")
            jellyfin_dict = {}

        # Process each series
        for series in all_series:
            # Basic info
            item = {
                'title': series['title'],
                'type': 'series',
                'year': series.get('year', ''),
                'path': series.get('path', ''),
                'sonarr_id': series['id'],
                'tags': [t for t in series.get('tags', [])],
            }

            # Quality (from first episode file if available)
            if series.get('statistics', {}).get('episodeFileCount', 0) > 0:
                # Get episode to check quality
                episodes = sonarr.get_episodes(series['id'])
                if episodes:
                    first_ep = next((e for e in episodes if e.get('hasFile')), None)
                    if first_ep and first_ep.get('episodeFile'):
                        quality_str = first_ep['episodeFile'].get('quality', {}).get('quality', {}).get('name', 'unknown')
                        item['quality'] = parse_quality(quality_str)

                        # Total size
                        size_bytes = first_ep['episodeFile'].get('size', 0) * series['statistics'].get('episodeFileCount', 1)
                        item['size_gb'] = size_bytes / (1024**3)
                    else:
                        item['quality'] = 'unknown'
                        item['size_gb'] = 0
                else:
                    item['quality'] = 'unknown'
                    item['size_gb'] = 0
            else:
                item['quality'] = 'unknown'
                item['size_gb'] = 0

            # Ratings
            ratings = series.get('ratings', {})
            if 'imdb' in ratings:
                item['rating'] = ratings['imdb'].get('value', 0.0)
                item['vote_count'] = ratings['imdb'].get('votes', 0)
            elif 'tmdb' in ratings:
                item['rating'] = ratings['tmdb'].get('value', 0.0)
                item['vote_count'] = ratings['tmdb'].get('votes', 0)
            else:
                item['rating'] = 0.0
                item['vote_count'] = 0

            # Age
            added_date = series.get('added')
            if added_date:
                added_dt = datetime.fromisoformat(added_date.replace('Z', '+00:00'))
                age_days = (datetime.now() - added_dt.replace(tzinfo=None)).days
                item['age_days'] = age_days
                item['age_months'] = age_days / 30
            else:
                item['age_days'] = 999
                item['age_months'] = 99

            # Watch status
            jellyfin_item = jellyfin_dict.get(series.get('path', ''))
            if jellyfin_item:
                item['played'] = jellyfin_item.get('UserData', {}).get('Played', False)
                last_played = jellyfin_item.get('UserData', {}).get('LastPlayedDate')
                if last_played:
                    item['last_played_date'] = datetime.fromisoformat(last_played.replace('Z', '+00:00')).replace(tzinfo=None)
            else:
                item['played'] = False
                item['last_played_date'] = None

            # Library context
            item['in_kids_library'] = 'kids' in series.get('path', '').lower()

            # Calculate score
            score, reason = calculate_deletion_score(item, config)
            item['score'] = score
            item['reason'] = reason

            results.append(item)

    except APIError as e:
        logger.error(f"API error while analyzing series: {e}")

    return results


def check_prowlarr_availability(items: List[Dict], config: Dict, logger) -> List[Dict]:
    """Check Prowlarr for item availability (re-acquisition difficulty).

    Args:
        items: List of items to check
        config: Configuration dictionary
        logger: Logger instance

    Returns:
        Updated items with indexer_count field
    """
    if not config['analyzer']['check_prowlarr']:
        logger.info("Prowlarr check disabled (set analyzer.check_prowlarr: true to enable)")
        for item in items:
            item['indexer_count'] = 0
        return items

    try:
        # Prowlarr searches can be slow, use longer timeout
        prowlarr = ProwlarrAPI(
            config['prowlarr']['url'],
            config['prowlarr']['api_key'],
            timeout=120  # 2 minutes for search operations
        )
        logger.info(f"Connecting to Prowlarr at {config['prowlarr']['url']}...")
        indexers = prowlarr.get_indexers()
        logger.info(f"Prowlarr: {len(indexers)} indexers available")

        logger.info(f"Checking {len(items)} items against Prowlarr (this may take a while)...")

        for idx, item in enumerate(items, 1):
            # Search for item
            try:
                query = item['title']
                logger.debug(f"[{idx}/{len(items)}] Searching Prowlarr for: '{query}'")

                start_time = time.time()
                results = prowlarr.search(query)
                elapsed = time.time() - start_time

                logger.debug(f"  → Search completed in {elapsed:.2f}s, found {len(results)} results")

                # Count unique indexers with results
                indexer_ids = set(r.get('indexerId') for r in results if r.get('indexerId'))
                item['indexer_count'] = len(indexer_ids)

                # Adjust score if rare (< 2 indexers)
                min_count = config['thresholds']['min_indexer_count']
                if item['indexer_count'] < min_count and item['score'] > 0:
                    item['score'] = int(item['score'] * 0.7)  # Reduce score by 30%
                    item['reason'] += f" (rare: {item['indexer_count']} indexers)"
                    logger.debug(f"  → Rare content: {item['indexer_count']} indexers, score reduced")

                # Progress logging
                if idx % 5 == 0:
                    logger.info(f"  Checked {idx}/{len(items)} items...")

                # Rate limiting: wait 0.5 seconds between requests
                time.sleep(0.5)

            except APIError as e:
                logger.error(f"APIError searching '{item['title']}': {e}")
                logger.error(f"  Error type: {type(e).__name__}")
                logger.error(f"  Error details: {str(e)}")
                item['indexer_count'] = 0
            except Exception as e:
                logger.error(f"Unexpected error searching '{item['title']}': {e}")
                logger.error(f"  Error type: {type(e).__name__}")
                logger.error(f"  Error details: {str(e)}")
                import traceback
                logger.error(f"  Traceback: {traceback.format_exc()}")
                item['indexer_count'] = 0

        logger.info(f"Prowlarr check completed for {len(items)} items")

    except APIError as e:
        logger.warning(f"Could not connect to Prowlarr: {e}")
        logger.warning("Skipping Prowlarr availability check")
        for item in items:
            item['indexer_count'] = 0
    except Exception as e:
        logger.error(f"Unexpected Prowlarr error: {e}")
        for item in items:
            item['indexer_count'] = 0

    return items


def export_report(items: List[Dict], output_path: str, logger) -> None:
    """Export analysis results to CSV.

    Args:
        items: List of analyzed items
        output_path: Path to output CSV file
        logger: Logger instance
    """
    logger.info(f"Exporting report to {output_path}")

    # Sort by score (descending)
    items_sorted = sorted(items, key=lambda x: x['score'], reverse=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'Title', 'Type', 'Quality', 'Size_GB', 'Score', 'Reason',
            'Last_Watched', 'IMDB_Rating', 'Age_Days', 'Indexer_Count', 'ID', 'Path'
        ])

        writer.writeheader()

        for item in items_sorted:
            last_watched = 'Never'
            if item.get('played') and item.get('last_played_date'):
                last_watched = item['last_played_date'].strftime('%Y-%m-%d')
            elif item.get('played'):
                last_watched = 'Yes'

            writer.writerow({
                'Title': item['title'],
                'Type': item['type'],
                'Quality': item.get('quality', 'unknown'),
                'Size_GB': f"{item.get('size_gb', 0):.2f}",
                'Score': item['score'],
                'Reason': item['reason'],
                'Last_Watched': last_watched,
                'IMDB_Rating': f"{item.get('rating', 0):.1f}",
                'Age_Days': item.get('age_days', 0),
                'Indexer_Count': item.get('indexer_count', 0),
                'ID': item.get('radarr_id') or item.get('sonarr_id', ''),
                'Path': item.get('path', '')
            })

    logger.info(f"Report exported: {len(items_sorted)} items")


def analyze_library(config: Dict, media_type: Optional[str] = None, output_path: Optional[str] = None) -> bool:
    """Analyze library and generate deletion candidate report.

    Args:
        config: Configuration dictionary
        media_type: Optional type filter ('movies' or 'series')
        output_path: Optional custom output path

    Returns:
        True if successful, False otherwise
    """
    logger = setup_logging('library_analyzer.log', level=config['logging']['level'])
    notifier = create_notifier(config)

    logger.info("="*60)
    logger.info("LIBRARY ANALYZER STARTED")
    logger.info("="*60)

    try:
        with acquire_lock('library_analyzer'):
            logger.info("Lock acquired, proceeding with analysis")

            all_items = []

            # Analyze movies
            if media_type is None or media_type == 'movies':
                movies = analyze_movies(config, logger)
                all_items.extend(movies)
                logger.info(f"Analyzed {len(movies)} movies")

            # Analyze series
            if media_type is None or media_type == 'series':
                series = analyze_series(config, logger)
                all_items.extend(series)
                logger.info(f"Analyzed {len(series)} series")

            # Check Prowlarr availability
            if all_items:
                all_items = check_prowlarr_availability(all_items, config, logger)

            # Generate report filename
            if output_path is None:
                date_str = datetime.now().strftime('%Y-%m-%d')
                output_path = os.path.join(
                    config['paths']['reports'],
                    f'library_analysis_{date_str}.csv'
                )

            # Export report
            export_report(all_items, output_path, logger)

            # Statistics
            threshold = config['thresholds']['deletion_score_threshold']
            candidates = [i for i in all_items if i['score'] >= threshold]

            logger.info("\n" + "="*60)
            logger.info("ANALYSIS SUMMARY")
            logger.info("="*60)
            logger.info(f"Total items analyzed: {len(all_items)}")
            logger.info(f"Deletion candidates (score >= {threshold}): {len(candidates)}")

            if candidates:
                total_size = sum(c.get('size_gb', 0) for c in candidates)
                logger.info(f"Potential space to free: {total_size:.2f} GB")

                logger.info(f"\nTop 10 candidates:")
                for i, item in enumerate(candidates[:10], 1):
                    logger.info(f"  {i}. {item['title']} (score: {item['score']}, {item.get('size_gb', 0):.1f}GB)")

            logger.info(f"\nFull report saved to: {output_path}")
            logger.info("="*60)
            logger.info("LIBRARY ANALYZER COMPLETED SUCCESSFULLY")
            logger.info("="*60)

            return True

    except Exception as e:
        error_msg = f"Unexpected error during analysis: {e}"
        logger.exception(error_msg)
        notifier.notify_error('library_analyzer', error_msg, details=str(e))
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Analyze library and calculate deletion scores',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze entire library
  %(prog)s --execute

  # Analyze movies only
  %(prog)s --execute --type movies

  # Analyze series only
  %(prog)s --execute --type series

  # Custom output file
  %(prog)s --execute --output /path/to/report.csv

Notes:
  - Generates CSV report with deletion scores (0-100)
  - Score >= 80: immediate candidates for deletion
  - Score 60-79: strong candidates (review recommended)
  - Score 40-59: moderate candidates
  - Score 0-39: keep
  - Checks Prowlarr for re-acquisition difficulty
  - Report saved to reports/library_analysis_YYYY-MM-DD.csv
  - NO DESTRUCTIVE OPERATIONS (safe to run)
        """
    )

    # Mode selection
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument('--dry-run', action='store_true', help='Same as --execute (no destructive ops)')
    mode_group.add_argument('--execute', action='store_true', help='Generate analysis report')

    parser.add_argument('--config', type=str, default='config.yaml', help='Path to configuration file')
    parser.add_argument('--type', type=str, choices=['movies', 'series'], help='Analyze only this type')
    parser.add_argument('--output', type=str, help='Custom output CSV path')

    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"ERROR: Failed to load configuration: {e}", file=sys.stderr)
        return 1

    # Run analysis
    success = analyze_library(config, media_type=args.type, output_path=args.output)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
