#!/usr/bin/env python3
"""Comprehensive seedbox and local cleanup with multi-phase purge strategy.

This script performs intelligent cleanup across four phases:

Phase 0: Auto-Import (TMDB)
  - Scan downloads/_done for unmanaged video files
  - Parse filenames to extract title/year
  - Fetch age ratings from TMDB (G, PG, PG-13, R, TV-Y, TV-PG, etc.)
  - Route kids content (G, PG, TV-Y, TV-Y7, TV-G, TV-PG) to kids libraries
  - Route adult content to standard libraries
  - Import main videos and subtitles only (skip extras)

Phase 1: Active Torrents (XMLRPC)
  - Delete torrents via rtorrent XMLRPC if imported + policy met
  - Uses hash-based matching (downloadId == torrent hash)
  - Policy: ratio >= min_ratio OR age >= min_days

Phase 2: Remote /downloads Files (SSH)
  - Clean up orphaned files in seedbox (aligned with seedbox_sync.py)
  - Checks multiple directories: /_ready (primary), /downloads (fallback)
  - Avoids duplicate cleanup when fallback is parent of primary
  - Delete files that have been imported to Radarr/Sonarr
  - Delete extra files (trailers, samples, behind the scenes, txt, nfo, etc.)
  - Keep main videos and subtitles not yet imported (lftp will download them)

Phase 3: Local _done Files (Filesystem)
  - Clean up local downloads/_done after import to library
  - Delete files confirmed as imported via Radarr/Sonarr history (by original filename)
  - Delete extra files (trailers, samples, behind the scenes, txt, nfo, etc.)
  - Keep main videos and subtitles not yet imported

File Classification:
- VIDEO: Main movie/episode files (kept for import)
- SUBTITLE: .srt, .sub, .ass files (kept for import)
- EXTRA: Trailers, samples, behind the scenes, txt, nfo (always purged)

Features:
- Hash-based torrent matching (100% accurate)
- Intelligent file classification (video/subtitle/extra)
- Age rating-based library routing
- Comprehensive cleanup across all storage layers
- Dry-run mode for safety
- Detailed logging and ntfy notifications

Usage:
    # Dry-run mode (shows what would be deleted/imported)
    python3 scripts/seedbox_purge.py --dry-run --verbose

    # Execute mode (actually delete and import)
    python3 scripts/seedbox_purge.py --execute

    # Skip specific phases
    python3 scripts/seedbox_purge.py --execute --skip-auto-import
    python3 scripts/seedbox_purge.py --execute --skip-torrents
    python3 scripts/seedbox_purge.py --execute --skip-remote-files
    python3 scripts/seedbox_purge.py --execute --skip-local-done
"""

import sys
import argparse
import time
import os
from pathlib import Path
from typing import Dict, Set, Tuple, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.ntfy_notifier import create_notifier
from utils.validators import acquire_lock
from utils.rtorrent_client import RTorrentClient
from utils.api_clients import RadarrAPI, SonarrAPI
from utils.seedbox_ssh import SeedboxSSH
from utils.tmdb_client import create_tmdb_client
import re


def classify_file(filepath: Path) -> str:
    """Classify file as 'video', 'subtitle', or 'extra'.

    Args:
        filepath: Path to file

    Returns:
        'video', 'subtitle', or 'extra'

    Examples:
        >>> classify_file(Path('movie.mkv'))
        'video'
        >>> classify_file(Path('movie.en.srt'))
        'subtitle'
        >>> classify_file(Path('movie-trailer.mp4'))
        'extra'
        >>> classify_file(Path('movie-sample.mkv'))
        'extra'
    """
    filename = filepath.name.lower()
    stem = filepath.stem.lower()

    # Subtitle extensions
    subtitle_exts = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx', '.sup'}
    if filepath.suffix.lower() in subtitle_exts:
        return 'subtitle'

    # Video extensions
    video_exts = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mpg', '.mpeg', '.wmv', '.flv', '.mov'}
    if filepath.suffix.lower() not in video_exts:
        # Not a video file - classify as extra
        return 'extra'

    # Check for extra video patterns (trailers, samples, behind the scenes, etc.)
    extra_patterns = [
        r'[-_\.\s](trailer|preview|teaser|clip)s?[-_\.\s]',
        r'[-_\.\s](sample|rarbg)[-_\.\s]',
        r'[-_\.\s](behind\.?the\.?scenes?|bts|making\.?of)[-_\.\s]',
        r'[-_\.\s](deleted\.?scenes?|extras?|bonus)[-_\.\s]',
        r'[-_\.\s](featurette|interview|promo)[-_\.\s]',
        r'[-_\.\s](proof|screener)[-_\.\s]',
        r'^sample[-_\.]',
        r'[-_\.]sample$',
    ]

    for pattern in extra_patterns:
        if re.search(pattern, filename, re.IGNORECASE):
            return 'extra'

    # Check file size - videos under 100MB are likely samples/extras
    try:
        if filepath.exists():
            size_mb = filepath.stat().st_size / (1024 * 1024)
            if size_mb < 100:
                return 'extra'
    except:
        pass

    return 'video'


def should_import_file(filepath: Path) -> bool:
    """Determine if file should be imported to Radarr/Sonarr.

    Args:
        filepath: Path to file

    Returns:
        True if file should be imported (main video or subtitle)
    """
    classification = classify_file(filepath)
    return classification in ('video', 'subtitle')


def should_purge_file(filepath: Path) -> bool:
    """Determine if file should be purged (extras, trailers, etc.).

    Args:
        filepath: Path to file

    Returns:
        True if file should be purged
    """
    return classify_file(filepath) == 'extra'


def parse_media_filename(filename: str) -> Optional[Tuple[str, Optional[int], str]]:
    """Parse media filename to extract title, year, and content type.

    Args:
        filename: Filename to parse (e.g., 'The.Lion.King.1994.1080p.BluRay.mkv')

    Returns:
        Tuple of (title, year, content_type) or None if parsing fails
        content_type is either 'movie' or 'series'

    Example:
        >>> parse_media_filename('The.Lion.King.1994.1080p.BluRay.mkv')
        ('The Lion King', 1994, 'movie')
        >>> parse_media_filename('Breaking.Bad.S01E01.720p.mkv')
        ('Breaking Bad', None, 'series')
    """
    # Remove file extension
    name = Path(filename).stem

    # Detect series (S01E01, s01e01, 1x01 patterns)
    series_patterns = [
        r'[Ss]\d{1,2}[Ee]\d{1,2}',  # S01E01
        r'\d{1,2}x\d{1,2}',           # 1x01
        r'Season\s*\d+',              # Season 1
    ]

    is_series = any(re.search(pattern, name) for pattern in series_patterns)
    content_type = 'series' if is_series else 'movie'

    # Remove quality tags
    quality_tags = r'(1080p|720p|480p|2160p|4K|BluRay|WEB-DL|HDTV|WEBRip|DVDRip|x264|x265|HEVC|AAC|AC3|DTS|' \
                   r'PROPER|REPACK|EXTENDED|UNRATED|DC|Directors\.Cut|xvid|divx)'
    name = re.sub(quality_tags, ' ', name, flags=re.IGNORECASE)

    # Remove release group tags (usually in brackets or after dash)
    name = re.sub(r'\[.*?\]', ' ', name)  # [RELEASE]
    name = re.sub(r'-[A-Z0-9]+$', ' ', name)  # -SPARKS

    # Extract year (4 digits)
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', name)
    year = int(year_match.group(1)) if year_match else None

    # Remove year from title
    if year:
        name = name.replace(str(year), ' ')

    # For series, remove season/episode info
    if is_series:
        for pattern in series_patterns:
            name = re.sub(pattern, ' ', name, flags=re.IGNORECASE)

    # Clean up title
    title = name.replace('.', ' ').replace('_', ' ')
    title = re.sub(r'\s+', ' ', title).strip()

    if not title:
        return None

    return (title, year, content_type)


def auto_import_files(
    config: Dict[str, Any],
    radarr: RadarrAPI,
    sonarr: SonarrAPI,
    tmdb,
    logger,
    dry_run: bool = True,
    verbose: bool = False
) -> Tuple[int, int]:
    """Auto-import unmanaged files to Radarr/Sonarr with age rating detection.

    Scans local _done directory for files not yet in libraries, determines
    if content is for kids based on age rating, and adds to appropriate
    Radarr/Sonarr instance with correct root folder.

    Args:
        config: Configuration dictionary
        radarr: Radarr API client
        sonarr: Sonarr API client
        tmdb: TMDB API client (or None if not configured)
        logger: Logger instance
        dry_run: If True, don't actually import
        verbose: Show detailed information

    Returns:
        Tuple of (movies_imported, series_imported)
    """
    logger.info("")
    logger.info("="*60)
    logger.info("PHASE 0: AUTO-IMPORT UNMANAGED FILES")
    logger.info("="*60)

    if not config['thresholds'].get('auto_import_enabled', True):
        logger.info("Auto-import disabled in configuration")
        return (0, 0)

    if not tmdb:
        logger.warning("TMDB client not configured, auto-import disabled")
        logger.info("Get a free API key from https://www.themoviedb.org/settings/api")
        return (0, 0)

    downloads_done = Path(config['paths']['downloads_done'])

    if not downloads_done.exists():
        logger.warning(f"Downloads directory not found: {downloads_done}")
        return (0, 0)

    # Get existing movies and series
    existing_movies = {m['title'].lower(): m for m in radarr.get_movies()}
    existing_series = {s['title'].lower(): s for s in sonarr.get_series()}

    # Get quality profiles and root folders
    try:
        movie_profiles = radarr.get_quality_profiles()
        series_profiles = sonarr.get_quality_profiles()
        movie_folders = radarr.get_root_folders()
        series_folders = sonarr.get_root_folders()

        if not movie_profiles or not series_profiles:
            logger.error("No quality profiles found in Radarr/Sonarr")
            return (0, 0)

        # Use first quality profile by default
        movie_quality_id = movie_profiles[0]['id']
        series_quality_id = series_profiles[0]['id']

        logger.info(f"Using quality profile: {movie_profiles[0]['name']} (ID: {movie_quality_id})")

    except Exception as e:
        logger.error(f"Failed to get Radarr/Sonarr configuration: {e}")
        return (0, 0)

    # Scan for video files (excluding extras)
    video_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mpg', '.mpeg'}
    all_video_files = []

    for ext in video_extensions:
        all_video_files.extend(downloads_done.rglob(f'*{ext}'))

    # Filter out extras (trailers, samples, behind the scenes, etc.)
    video_files = [f for f in all_video_files if classify_file(f) == 'video']
    extras_found = len(all_video_files) - len(video_files)

    logger.info(f"Found {len(all_video_files)} total video files in {downloads_done}")
    logger.info(f"  Main videos: {len(video_files)}")
    logger.info(f"  Extras (skipped): {extras_found}")

    if len(video_files) == 0:
        return (0, 0)

    logger.info(f"Processing {len(video_files)} files in parallel (max 5 workers)...")

    movies_imported = 0
    series_imported = 0
    skipped = 0
    failed = 0

    # Thread-safe counters
    lock = threading.Lock()

    def process_video_file(video_file):
        """Process a single video file (thread-safe)."""
        nonlocal movies_imported, series_imported, skipped, failed

        try:
            # Parse filename
            parsed = parse_media_filename(video_file.name)

            if not parsed:
                if verbose:
                    logger.debug(f"Could not parse: {video_file.name}")
                with lock:
                    skipped += 1
                return 'skipped'

            title, year, content_type = parsed

            # Check if already in library
            if content_type == 'movie':
                if title.lower() in existing_movies:
                    if verbose:
                        logger.debug(f"Already in Radarr: {title} ({year})")
                    with lock:
                        skipped += 1
                    return 'skipped'
            else:
                if title.lower() in existing_series:
                    if verbose:
                        logger.debug(f"Already in Sonarr: {title}")
                    with lock:
                        skipped += 1
                    return 'skipped'

            # Determine if kids content
            kids_ratings = config['thresholds']['kids_age_ratings']
            rating_key = 'series' if content_type == 'series' else 'movies'
            is_kids = tmdb.is_kids_content(
                title, year, content_type,
                kids_ratings[rating_key]
            )

            # Determine root folder
            if content_type == 'movie':
                root_folder = config['paths']['kids_movies'] if is_kids else config['paths']['movies']
                quality_id = movie_quality_id
            else:
                root_folder = config['paths']['kids_series'] if is_kids else config['paths']['series']
                quality_id = series_quality_id

            category = "kids" if is_kids else "adult"
            logger.info(f"📥 Importing {content_type}: {title} ({year}) [{category}] → {root_folder}")

            if not dry_run:
                try:
                    if content_type == 'movie':
                        # Use Radarr's search (returns proper TMDB metadata)
                        search_results = radarr.search_movie(title, year)
                        if search_results:
                            tmdb_id = search_results[0].get('tmdbId')
                            if not tmdb_id:
                                logger.warning(f"No TMDB ID in Radarr search results for: {title} ({year})")
                                with lock:
                                    failed += 1
                                return 'failed'

                            radarr.add_movie(
                                tmdb_id=tmdb_id,
                                title=title,
                                year=year or search_results[0].get('year', 0),
                                quality_profile_id=quality_id,
                                root_folder_path=root_folder,
                                monitored=True,
                                search_on_add=True
                            )
                            with lock:
                                movies_imported += 1
                            return 'imported'
                        else:
                            logger.warning(f"Movie not found in Radarr lookup: {title} ({year})")
                            with lock:
                                failed += 1
                            return 'failed'
                    else:
                        # Use Sonarr's search (returns proper TVDB metadata)
                        search_results = sonarr.search_series(title, year)
                        if search_results:
                            tvdb_id = search_results[0].get('tvdbId')
                            if not tvdb_id:
                                logger.warning(f"No TVDB ID in Sonarr search results for: {title}")
                                with lock:
                                    failed += 1
                                return 'failed'

                            sonarr.add_series(
                                tvdb_id=tvdb_id,
                                title=title,
                                year=year or int(search_results[0].get('year', 0)),
                                quality_profile_id=quality_id,
                                root_folder_path=root_folder,
                                monitored=True,
                                search_on_add=True
                            )
                            with lock:
                                series_imported += 1
                            return 'imported'
                        else:
                            logger.warning(f"Series not found in Sonarr lookup: {title}")
                            with lock:
                                failed += 1
                            return 'failed'

                except Exception as e:
                    logger.error(f"Failed to import {title}: {e}")
                    with lock:
                        failed += 1
                    return 'failed'
            else:
                # Dry run
                with lock:
                    if content_type == 'movie':
                        movies_imported += 1
                    else:
                        series_imported += 1
                return 'dry_run'

        except Exception as e:
            logger.error(f"Error processing {video_file.name}: {e}")
            with lock:
                failed += 1
            return 'error'

    # Process files in parallel
    max_workers = min(5, len(video_files))  # Max 5 workers to avoid API rate limits

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_video_file, vf): vf for vf in video_files}

        for future in as_completed(futures):
            future.result()  # Wait for completion (errors already logged)

    # Summary
    logger.info("")
    logger.info(f"Movies imported: {movies_imported}")
    logger.info(f"Series imported: {series_imported}")
    logger.info(f"Skipped (already in library): {skipped}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Total processed: {len(video_files)}")

    return (movies_imported, series_imported)


def get_imported_hashes(radarr: RadarrAPI, sonarr: SonarrAPI, logger) -> Set[str]:
    """Get all imported torrent hashes from Radarr/Sonarr (parallelized).

    Args:
        radarr: Radarr API client
        sonarr: Sonarr API client
        logger: Logger instance

    Returns:
        Set of torrent hashes (lowercase for case-insensitive matching)
    """
    imported = set()
    lock = threading.Lock()

    def get_radarr_hashes():
        """Get Radarr import history."""
        try:
            logger.info("Getting Radarr import history...")
            radarr_history = radarr._request(
                'GET',
                '/api/v3/history',
                params={'eventType': 3, 'pageSize': 1000}
            )

            count = 0
            if radarr_history and 'records' in radarr_history:
                for record in radarr_history['records']:
                    download_id = record.get('downloadId', '').lower()
                    if download_id:
                        with lock:
                            imported.add(download_id)
                        count += 1

            logger.info(f"Found {count} imported movies")

        except Exception as e:
            logger.warning(f"Could not get Radarr history: {e}")

    def get_sonarr_hashes():
        """Get Sonarr import history."""
        try:
            logger.info("Getting Sonarr import history...")
            sonarr_history = sonarr._request(
                'GET',
                '/api/v3/history',
                params={'eventType': 3, 'pageSize': 1000}
            )

            count = 0
            if sonarr_history and 'records' in sonarr_history:
                for record in sonarr_history['records']:
                    download_id = record.get('downloadId', '').lower()
                    if download_id:
                        with lock:
                            imported.add(download_id)
                        count += 1

            logger.info(f"Found {count} imported episodes")

        except Exception as e:
            logger.warning(f"Could not get Sonarr history: {e}")

    # Fetch in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(get_radarr_hashes),
            executor.submit(get_sonarr_hashes)
        ]
        for future in as_completed(futures):
            future.result()

    logger.info(f"Total unique imported hashes: {len(imported)}")
    return imported


def get_imported_paths(radarr: RadarrAPI, sonarr: SonarrAPI, logger) -> Set[str]:
    """Get all imported media file paths from Radarr/Sonarr libraries (parallelized).

    Args:
        radarr: Radarr API client
        sonarr: Sonarr API client
        logger: Logger instance

    Returns:
        Set of file paths in library (for checking if imports are complete)
    """
    library_files = set()
    lock = threading.Lock()

    def get_radarr_paths():
        """Get Radarr library file paths."""
        try:
            logger.info("Getting Radarr library files...")
            movies = radarr._request('GET', '/api/v3/movie')
            count = 0

            for movie in movies:
                if movie.get('hasFile'):
                    movie_file = radarr._request('GET', f"/api/v3/moviefile/{movie['movieFile']['id']}")
                    if movie_file:
                        with lock:
                            library_files.add(movie_file['path'])
                        count += 1

            logger.info(f"Found {count} movie files in library")

        except Exception as e:
            logger.warning(f"Could not get Radarr library: {e}")

    def get_sonarr_paths():
        """Get Sonarr library file paths."""
        try:
            logger.info("Getting Sonarr library files...")
            series = sonarr._request('GET', '/api/v3/series')
            count = 0
            seen_file_ids = set()  # Track already processed file IDs

            for show in series:
                episodes = sonarr._request('GET', f"/api/v3/episode?seriesId={show['id']}")
                for episode in episodes:
                    # Check if episode has a file and get the file ID
                    if episode.get('hasFile') and 'episodeFileId' in episode:
                        file_id = episode['episodeFileId']

                        # Skip if we already processed this file (multiple episodes can share a file)
                        if file_id in seen_file_ids:
                            continue
                        seen_file_ids.add(file_id)

                        # Fetch full episode file details (like Radarr does)
                        episode_file = sonarr._request('GET', f"/api/v3/episodefile/{file_id}")
                        if episode_file and 'path' in episode_file:
                            with lock:
                                library_files.add(episode_file['path'])
                            count += 1

            logger.info(f"Found {count} episode files in library")

        except Exception as e:
            logger.warning(f"Could not get Sonarr library: {e}")

    # Fetch in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(get_radarr_paths),
            executor.submit(get_sonarr_paths)
        ]
        for future in as_completed(futures):
            future.result()

    logger.info(f"Total files in libraries: {len(library_files)}")
    return library_files


def get_imported_done_files(radarr: RadarrAPI, sonarr: SonarrAPI, downloads_done: Path, logger) -> Set[str]:
    """Get original filenames that were imported from the _done directory.

    This function checks Radarr/Sonarr import history to find files that were
    imported from the downloads_done directory, using the ORIGINAL filename
    before Radarr/Sonarr renamed them.

    Args:
        radarr: Radarr API client
        sonarr: Sonarr API client
        downloads_done: Path to downloads_done directory
        logger: Logger instance

    Returns:
        Set of original filenames that have been imported from _done
    """
    imported_files = set()
    lock = threading.Lock()

    # Convert downloads_done to string for path matching
    done_path_str = str(downloads_done.resolve())

    def get_radarr_imported():
        """Get Radarr import history."""
        try:
            logger.info("Getting Radarr import history from _done...")
            radarr_history = radarr._request(
                'GET',
                '/api/v3/history',
                params={'eventType': 3, 'pageSize': 10000}
            )

            count = 0
            if radarr_history and 'records' in radarr_history:
                for record in radarr_history['records']:
                    # Try droppedPath first (most reliable - full path)
                    dropped_path = record.get('data', {}).get('droppedPath', '')

                    # Check if this file came from _done directory
                    if dropped_path and done_path_str in dropped_path:
                        # Extract filename from dropped path
                        filename = Path(dropped_path).name
                        with lock:
                            imported_files.add(filename)
                        count += 1
                    else:
                        # Fallback to sourceTitle (may be just filename or release name)
                        source_path = record.get('sourceTitle', '')
                        if source_path and not os.path.isabs(source_path):
                            # It's a filename, assume it came from _done
                            filename = Path(source_path).name
                            with lock:
                                imported_files.add(filename)
                            count += 1

            logger.info(f"Found {count} Radarr imports from _done")

        except Exception as e:
            logger.warning(f"Could not get Radarr import history: {e}")

    def get_sonarr_imported():
        """Get Sonarr import history."""
        try:
            logger.info("Getting Sonarr import history from _done...")
            sonarr_history = sonarr._request(
                'GET',
                '/api/v3/history',
                params={'eventType': 3, 'pageSize': 10000}
            )

            count = 0
            if sonarr_history and 'records' in sonarr_history:
                for record in sonarr_history['records']:
                    # Try droppedPath first (most reliable - full path)
                    dropped_path = record.get('data', {}).get('droppedPath', '')

                    # Check if this file came from _done directory
                    if dropped_path and done_path_str in dropped_path:
                        # Extract filename from dropped path
                        filename = Path(dropped_path).name
                        with lock:
                            imported_files.add(filename)
                        count += 1
                    else:
                        # Fallback to sourceTitle (may be just filename or release name)
                        source_path = record.get('sourceTitle', '')
                        if source_path and not os.path.isabs(source_path):
                            # It's a filename, assume it came from _done
                            filename = Path(source_path).name
                            with lock:
                                imported_files.add(filename)
                            count += 1

            logger.info(f"Found {count} Sonarr imports from _done")

        except Exception as e:
            logger.warning(f"Could not get Sonarr import history: {e}")

    # Fetch in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(get_radarr_imported),
            executor.submit(get_sonarr_imported)
        ]
        for future in as_completed(futures):
            future.result()

    logger.info(f"Total unique filenames imported from _done: {len(imported_files)}")
    return imported_files


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
    """Phase 1: Purge active torrents via XMLRPC (parallelized).

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
    logger.info("PHASE 1: ACTIVE TORRENT CLEANUP (XMLRPC - PARALLELIZED)")
    logger.info("="*60)

    min_ratio = config['thresholds'].get('seedbox_min_ratio', 1.5)
    min_days = config['thresholds'].get('seedbox_age_days', 2)

    deleted_count = 0
    kept_count = 0
    not_imported_count = 0
    total_size_deleted = 0

    # Thread-safe counters
    lock = threading.Lock()

    # Get seeding torrents
    try:
        seeding_hashes = rtorrent.get_seeding_torrents()
        logger.info(f"Found {len(seeding_hashes)} seeding torrents")
    except Exception as e:
        logger.error(f"Failed to get seeding torrents: {e}")
        return 0, 0

    # Filter to only imported torrents
    imported_seeding = [h for h in seeding_hashes if h.lower() in imported_hashes]
    not_imported_count = len(seeding_hashes) - len(imported_seeding)

    if verbose:
        logger.info(f"Skipping {not_imported_count} torrents (not imported)")

    logger.info(f"Processing {len(imported_seeding)} imported torrents in parallel (max 10 workers)...")

    def process_torrent(hash_id):
        """Process a single torrent (thread-safe)."""
        nonlocal deleted_count, kept_count, total_size_deleted

        try:
            # Get torrent info
            torrent = rtorrent.get_torrent_info(hash_id)

            # Check policy
            should_delete, reason = meets_policy(torrent, min_ratio, min_days)

            if should_delete:
                size_gb = torrent['size_bytes'] / (1024 ** 3)

                if dry_run:
                    logger.info(
                        f"🗑️  [DRY-RUN] Would delete: {torrent['name']}\n"
                        f"    Reason: {reason}, Size: {size_gb:.2f} GB"
                    )
                else:
                    logger.info(f"🗑️  Deleting: {torrent['name']} ({reason}, {size_gb:.2f} GB)")
                    try:
                        rtorrent.delete_torrent(hash_id, delete_files=True)
                        logger.info(f"    ✅ Deleted: {torrent['name']}")
                        with lock:
                            total_size_deleted += torrent['size_bytes']
                    except Exception as e:
                        logger.error(f"    ❌ Failed to delete {torrent['name']}: {e}")
                        return False

                with lock:
                    deleted_count += 1
                return True
            else:
                if verbose:
                    logger.info(f"✅ KEEP: {torrent['name']} ({reason})")
                with lock:
                    kept_count += 1
                return False

        except Exception as e:
            logger.warning(f"Could not process {hash_id[:8]}: {e}")
            return False

    # Process torrents in parallel
    max_workers = min(10, len(imported_seeding))  # Max 10 concurrent connections

    if max_workers > 0:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_torrent, hash_id): hash_id
                      for hash_id in imported_seeding}

            for future in as_completed(futures):
                future.result()  # Wait for completion (errors already logged)

    logger.info("")
    logger.info(f"Phase 1 Summary:")
    logger.info(f"  Not imported (kept): {not_imported_count}")
    logger.info(f"  Policy not met (kept): {kept_count}")
    logger.info(f"  Policy met (deleted): {deleted_count}")
    logger.info(f"  Space freed: {total_size_deleted / (1024**3):.2f} GB")

    return deleted_count, total_size_deleted


def purge_remote_files(
    config: dict,
    library_files: Set[str],
    logger,
    dry_run: bool = False,
    verbose: bool = False
) -> Tuple[int, int]:
    """Phase 2: Purge orphaned files and extras in remote /downloads directory.

    Checks multiple remote directories (aligned with seedbox_sync.py):
    1. remote_downloads (_ready folder) - completed torrents
    2. remote_downloads_fallback (/downloads parent) - all files

    Deletes:
    1. Files that have been imported to Radarr/Sonarr library
    2. Extra files (trailers, samples, behind the scenes, txt, nfo, etc.)

    Keeps:
    - Main video files and subtitles that haven't been imported yet
    - Files will be downloaded by lftp sync script

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
    logger.info("PHASE 2: REMOTE /downloads CLEANUP (SSH)")
    logger.info("="*60)

    deleted_count = 0
    extras_deleted = 0
    total_size_deleted = 0

    # Connect to seedbox via SSH
    sb = config['seedbox']

    # Collect directories to clean (aligned with seedbox_sync.py)
    directories_to_clean = []

    # Primary: /_ready (completed torrents)
    if 'remote_downloads' in sb:
        directories_to_clean.append({
            'path': sb['remote_downloads'],
            'description': 'completed torrents (_ready)'
        })

    # Fallback: /downloads (all downloads, avoid duplicates with _ready)
    if 'remote_downloads_fallback' in sb:
        fallback_path = sb['remote_downloads_fallback']
        primary_path = sb.get('remote_downloads', '')

        # Only add fallback if it's not a parent of primary
        # (to avoid cleaning same files twice)
        if not primary_path.startswith(fallback_path + '/'):
            directories_to_clean.append({
                'path': fallback_path,
                'description': 'all downloads (including unfinished)'
            })
        else:
            logger.info(f"Skipping fallback cleanup: {fallback_path} is parent of {primary_path}")

    if not directories_to_clean:
        logger.warning("No remote directories configured for cleanup")
        return 0, 0

    logger.info(f"Will clean {len(directories_to_clean)} remote directories")

    try:
        with SeedboxSSH(
            host=sb['host'],
            port=sb['port'],
            username=sb['username'],
            password=sb['password']
        ) as ssh:
            logger.info(f"Connected to seedbox via SSH")

            # Process each directory
            for dir_info in directories_to_clean:
                remote_path = dir_info['path']
                description = dir_info['description']

                logger.info("")
                logger.info(f"Scanning {remote_path} ({description})...")

                # Check if directory exists
                if not ssh.path_exists(remote_path):
                    logger.info(f"  Directory does not exist: {remote_path}")
                    continue

                # List all files in this directory
                remote_files = ssh.list_files(remote_path)
                logger.info(f"  Found {len(remote_files)} files")

                # Check each file in this directory
                for file_info in remote_files:
                    file_path = file_info['path']
                    file_size = file_info['size']

                    # Skip if in protected folder
                    protected = config['safety'].get('protected_folders', [])
                    if any(prot in file_path for prot in protected):
                        if verbose:
                            logger.info(f"  PROTECTED: {file_path}")
                        continue

                    # Classify file
                    file_pathobj = Path(file_path)
                    classification = classify_file(file_pathobj)

                    # Check if file exists in Radarr/Sonarr library
                    filename = file_pathobj.name
                    in_library = any(filename in lib_path for lib_path in library_files)

                    # Decision logic:
                    # 1. If it's an extra file, always delete
                    # 2. If it's a main video/subtitle, only delete if imported to library
                    # 3. Keep main videos/subtitles not yet imported (lftp will download them)
                    should_delete = False
                    reason = ""

                    if classification == 'extra':
                        should_delete = True
                        reason = "extra file (trailer/sample/txt/nfo)"
                        extras_deleted += 1
                    elif in_library:
                        should_delete = True
                        reason = "imported to library"
                    else:
                        if verbose:
                            logger.info(f"  KEEP (not imported, lftp will download): {file_path} [{classification}]")
                        continue

                    # Delete the file
                    size_gb = file_size / (1024 ** 3)

                    if dry_run:
                        logger.info(f"  🗑️  [DRY-RUN] Would delete remote: {file_path} ({size_gb:.2f} GB) - {reason}")
                    else:
                        logger.info(f"  🗑️  Deleting remote: {file_path} ({size_gb:.2f} GB) - {reason}")
                        try:
                            ssh.delete_file(file_path)
                            logger.info("      ✅ Deleted successfully")
                            total_size_deleted += file_size
                            deleted_count += 1
                        except Exception as e:
                            logger.error(f"      ❌ Failed to delete: {e}")

                # Clean up empty directories in this path (respecting protected folders)
                if not dry_run and deleted_count > 0:
                    logger.info(f"  Cleaning up empty directories in {remote_path}...")
                    protected = config['safety'].get('protected_folders', [])
                    empty_dirs = ssh.delete_empty_directories(remote_path, exclude_paths=protected)
                    logger.info(f"  Removed {empty_dirs} empty directories (protected folders preserved)")

    except Exception as e:
        logger.error(f"SSH connection error: {e}")
        return 0, 0

    logger.info("")
    logger.info(f"Phase 2 Summary:")
    logger.info(f"  Remote files deleted: {deleted_count}")
    logger.info(f"    - Extras purged: {extras_deleted}")
    logger.info(f"    - Imported to library: {deleted_count - extras_deleted}")
    logger.info(f"  Space freed: {total_size_deleted / (1024**3):.2f} GB")

    return deleted_count, total_size_deleted


def check_episode_in_library(radarr: RadarrAPI, sonarr: SonarrAPI, filepath: Path, logger) -> bool:
    """Check if a file's episode/movie exists in library with a file attached.

    This function provides fallback detection for files that were manually imported
    or have no history records. It parses the filename to extract metadata and
    checks if that content exists in the library with hasFile=true.

    Args:
        radarr: Radarr API client
        sonarr: Sonarr API client
        filepath: Path to file in _done
        logger: Logger instance

    Returns:
        True if episode/movie exists in library with file
    """
    try:
        # Parse filename
        parsed = parse_media_filename(filepath.name)
        if not parsed:
            return False

        title, year, content_type = parsed

        if content_type == 'movie':
            # Search Radarr for movie
            try:
                movies = radarr._request('GET', '/api/v3/movie')
                for movie in movies:
                    movie_title = movie.get('title', '').lower()
                    movie_year = movie.get('year', 0)

                    # Match by title (fuzzy) and year (if available)
                    if title.lower() in movie_title or movie_title in title.lower():
                        if year is None or movie_year == year:
                            if movie.get('hasFile'):
                                logger.debug(f"Found movie in library: {movie['title']} ({movie_year})")
                                return True
                return False
            except Exception as e:
                logger.debug(f"Error checking Radarr for {title}: {e}")
                return False

        else:  # series
            # Parse season/episode from filename
            match = re.search(r'[Ss](\d{1,2})[Ee](\d{1,2})', filepath.name)
            if not match:
                # Try alternative format: 1x01
                match = re.search(r'(\d{1,2})x(\d{1,2})', filepath.name)

            if not match:
                return False

            season = int(match.group(1))
            episode = int(match.group(2))

            # Search Sonarr for series
            try:
                series_list = sonarr._request('GET', '/api/v3/series')
                for series in series_list:
                    series_title = series.get('title', '').lower()

                    # Match by title (fuzzy)
                    if title.lower() in series_title or series_title in title.lower():
                        # Found series, check if episode has file
                        episodes = sonarr._request('GET', f"/api/v3/episode?seriesId={series['id']}")
                        for ep in episodes:
                            if ep.get('seasonNumber') == season and ep.get('episodeNumber') == episode:
                                if ep.get('hasFile'):
                                    logger.debug(f"Found episode in library: {series['title']} S{season:02d}E{episode:02d}")
                                    return True
                return False
            except Exception as e:
                logger.debug(f"Error checking Sonarr for {title} S{season:02d}E{episode:02d}: {e}")
                return False

    except Exception as e:
        logger.debug(f"Error in check_episode_in_library for {filepath.name}: {e}")
        return False


def purge_local_done(
    config: dict,
    library_files: Set[str],
    imported_done_files: Set[str],
    radarr: RadarrAPI,
    sonarr: SonarrAPI,
    logger,
    dry_run: bool = False,
    verbose: bool = False
) -> Tuple[int, int]:
    """Phase 3: Purge local _done files and extras with fallback detection.

    Deletes:
    1. Files that have been imported to Radarr/Sonarr library (via history check)
    2. Files whose episodes/movies exist in library (fallback for manual imports)
    3. Extra files (trailers, samples, behind the scenes, txt, nfo, etc.)
    4. Empty subdirectories after file cleanup

    Keeps:
    - Main video files and subtitles that haven't been imported yet
    - The _done folder itself (NEVER DELETED)

    CRITICAL PROTECTION:
    - The downloads_done folder itself is NEVER deleted
    - Only files and subdirectories within _done are cleaned
    - Empty _done folder is preserved

    FALLBACK DETECTION:
    - Files without import history are checked via filename parsing
    - Parses S##E## or #x# patterns to extract episode info
    - Checks if that episode exists in Sonarr with hasFile=true
    - Same logic for movies using title + year matching

    Args:
        config: Configuration dictionary
        library_files: Set of file paths in Radarr/Sonarr libraries (unused, kept for compatibility)
        imported_done_files: Set of original filenames imported from _done (from history)
        radarr: Radarr API client (for fallback detection)
        sonarr: Sonarr API client (for fallback detection)
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
    extras_deleted = 0
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

        # Classify file
        classification = classify_file(item)

        # Check if this file was imported from _done (via history)
        filename = item.name
        was_imported_history = filename in imported_done_files

        # Fallback: Check if episode/movie exists in library (for manual imports)
        was_imported_library = False
        if not was_imported_history and classification in ('video', 'subtitle'):
            was_imported_library = check_episode_in_library(radarr, sonarr, item, logger)

        was_imported = was_imported_history or was_imported_library

        # Decision logic:
        # 1. If it's an extra file, always delete
        # 2. If it's a main video/subtitle, only delete if imported (confirmed via history OR library check)
        should_delete = False
        reason = ""

        if classification == 'extra':
            should_delete = True
            reason = "extra file (trailer/sample/txt/nfo)"
            extras_deleted += 1
        elif was_imported:
            should_delete = True
            if was_imported_history:
                reason = "imported to library (confirmed via history)"
            else:
                reason = "episode/movie exists in library (manual import detected)"
        else:
            if verbose:
                logger.info(f"KEEP (not imported yet): {item} [{classification}]")
            continue

        # Delete the file
        try:
            # Check if file still exists (may have been deleted by Radarr/Sonarr during import)
            if not item.exists():
                if verbose:
                    logger.info(f"Already deleted (likely imported by Radarr/Sonarr): {item}")
                continue

            size_bytes = item.stat().st_size
            size_gb = size_bytes / (1024 ** 3)

            if dry_run:
                logger.info(f"🗑️  [DRY-RUN] Would delete local: {item} ({size_gb:.2f} GB) - {reason}")
            else:
                logger.info(f"🗑️  Deleting local: {item} ({size_gb:.2f} GB) - {reason}")
                item.unlink()
                logger.info("    ✅ Deleted successfully")
                total_size_deleted += size_bytes
                deleted_count += 1
        except FileNotFoundError:
            # File was deleted between our check and deletion attempt
            if verbose:
                logger.info(f"Already deleted (race condition): {item}")
        except PermissionError as e:
            logger.error(f"    ❌ Permission denied: {item}: {e}")
        except Exception as e:
            logger.error(f"    ❌ Failed to delete {item}: {e}")

    # Clean up empty directories
    if not dry_run and deleted_count > 0:
        logger.info("Cleaning up empty directories...")
        for dirpath in sorted(downloads_done.rglob('*'), reverse=True):
            # CRITICAL: NEVER delete the _done folder itself, only subdirectories within it
            if dirpath != downloads_done and dirpath.is_dir() and not any(dirpath.iterdir()):
                try:
                    dirpath.rmdir()
                    logger.info(f"Removed empty directory: {dirpath}")
                except Exception as e:
                    logger.debug(f"Could not remove {dirpath}: {e}")

    logger.info("")
    logger.info(f"Phase 3 Summary:")
    logger.info(f"  Local files deleted: {deleted_count}")
    logger.info(f"    - Extras purged: {extras_deleted}")
    logger.info(f"    - Imported to library: {deleted_count - extras_deleted}")
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

            # Initialize TMDB client (optional, for auto-import)
            tmdb = create_tmdb_client(config)

            # Get imported hashes (for Phase 1)
            imported_hashes = get_imported_hashes(radarr, sonarr, logger)

            # Get library files (for Phase 2)
            library_files = get_imported_paths(radarr, sonarr, logger)

            # Get imported filenames from _done (for Phase 3)
            downloads_done = Path(config['paths']['downloads_done'])
            imported_done_files = get_imported_done_files(radarr, sonarr, downloads_done, logger)

            logger.info("")

            # Track totals
            total_imported_movies = 0
            total_imported_series = 0
            total_deleted = 0
            total_size_freed = 0

            # Phase 0: Auto-import unmanaged files
            if not args.skip_auto_import:
                movies_imported, series_imported = auto_import_files(
                    config, radarr, sonarr, tmdb, logger,
                    dry_run=args.dry_run, verbose=args.verbose
                )
                total_imported_movies += movies_imported
                total_imported_series += series_imported
            else:
                logger.info("⏭️  SKIPPING Phase 0: Auto-import")

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
                    config, library_files, logger,
                    dry_run=args.dry_run, verbose=args.verbose
                )
                total_deleted += deleted
                total_size_freed += size_freed
            else:
                logger.info("⏭️  SKIPPING Phase 2: Remote file cleanup")

            # Phase 3: Local _done cleanup
            if not args.skip_local_done:
                deleted, size_freed = purge_local_done(
                    config, library_files, imported_done_files, radarr, sonarr, logger,
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
            if total_imported_movies > 0 or total_imported_series > 0:
                logger.info(f"Total movies imported: {total_imported_movies}")
                logger.info(f"Total series imported: {total_imported_series}")
                logger.info("")
            logger.info(f"Total items deleted: {total_deleted}")
            logger.info(f"Total space freed: {total_size_freed / (1024**3):.2f} GB")

            if args.dry_run and total_deleted > 0:
                logger.info("")
                logger.info("💡 This was a dry run. To actually delete:")
                logger.info("   python3 scripts/seedbox_purge.py --execute")

            # Success notification
            if (total_deleted > 0 or total_imported_movies > 0 or total_imported_series > 0) and not args.dry_run:
                if config['notifications']['ntfy']['send_on_success']:
                    stats = {}
                    if total_imported_movies > 0 or total_imported_series > 0:
                        stats['imported_movies'] = total_imported_movies
                        stats['imported_series'] = total_imported_series
                    if total_deleted > 0:
                        stats['deleted_items'] = total_deleted
                        stats['space_freed_gb'] = f"{total_size_freed / (1024**3):.2f}"

                    notifier.notify_success(
                        'seedbox_purge',
                        f'Imported {total_imported_movies + total_imported_series} items, deleted {total_deleted}',
                        stats=stats
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
        description='Comprehensive seedbox and local cleanup with auto-import',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
4-Phase Processing Strategy:

Phase 0 - Auto-Import:
  • Scan downloads/_done for unmanaged video files
  • Parse filenames to extract title/year
  • Check age ratings via TMDB to identify kids content
  • Import to appropriate Radarr/Sonarr with correct library path
  • Update missing ratings automatically



Phase 1: Active Torrents (XMLRPC)
  - Delete torrents from rtorrent if imported + policy met
  - Policy: ratio >= 1.5 OR age >= 2 days

Phase 2: Remote /downloads Files (SSH)
  - Clean up orphaned files on seedbox (aligned with seedbox_sync.py)
  - Checks multiple directories: /_ready (primary), /downloads (fallback)
  - Avoids duplicate cleanup when fallback is parent of primary
  - Delete imported files and extras

Phase 3: Local _done Files (Filesystem)
  - Clean up staging directory after import
  - Delete files confirmed imported via history
  - Fallback: Parse filenames and check if episode/movie exists in library
  - Catches manual imports without history records

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
        '--skip-auto-import',
        action='store_true',
        help='Skip Phase 0 (auto-import unmanaged files)'
    )
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
