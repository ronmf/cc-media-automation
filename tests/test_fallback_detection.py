#!/usr/bin/env python3
"""Test fallback detection for manually imported files without history.

This script demonstrates how the new fallback detection works in Phase 3
of seedbox_purge.py. It shows how the system can detect files that were
manually imported (no history records) by parsing filenames and checking
if the episode/movie exists in the library.

Usage:
    python3 tests/test_fallback_detection.py
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.seedbox_purge import parse_media_filename, check_episode_in_library
from utils.api_clients import RadarrAPI, SonarrAPI
from utils.config_loader import load_config
from utils.logger import setup_logging


def test_filename_parsing():
    """Test filename parsing function."""
    print("=" * 60)
    print("TESTING FILENAME PARSING")
    print("=" * 60)

    test_cases = [
        "My Hero Academia S08E08 VOSTFR 1080p WEB x264 AAC -Tsundere-Raws (CR).mkv",
        "Breaking.Bad.S01E01.720p.BluRay.x264.mkv",
        "The.Lion.King.1994.1080p.BluRay.mkv",
        "Avatar.The.Last.Airbender.1x01.720p.mkv",
        "Stranger Things Season 4 Episode 1.mkv",
    ]

    for filename in test_cases:
        result = parse_media_filename(filename)
        if result:
            title, year, content_type = result
            print(f"\n✅ {filename}")
            print(f"   Title: {title}")
            print(f"   Year: {year}")
            print(f"   Type: {content_type}")
        else:
            print(f"\n❌ Failed to parse: {filename}")


def test_fallback_detection():
    """Test fallback detection with actual API clients."""
    print("\n")
    print("=" * 60)
    print("TESTING FALLBACK DETECTION")
    print("=" * 60)

    # Load configuration
    try:
        config = load_config('config.yaml')
    except Exception as e:
        print(f"\n❌ Failed to load config.yaml: {e}")
        print("Make sure config.yaml exists and has valid credentials")
        return

    # Initialize logger
    logger = setup_logging('test_fallback.log', level='INFO')

    # Initialize API clients
    try:
        radarr = RadarrAPI(
            config['radarr']['url'],
            config['radarr']['api_key']
        )
        sonarr = SonarrAPI(
            config['sonarr']['url'],
            config['sonarr']['api_key']
        )
        print("\n✅ Connected to Radarr and Sonarr")
    except Exception as e:
        print(f"\n❌ Failed to connect to APIs: {e}")
        return

    # Test cases (replace with actual files from your _done directory)
    test_files = [
        Path("/mnt/media/downloads/_done/My Hero Academia S08E08 VOSTFR 1080p WEB x264 AAC -Tsundere-Raws (CR).mkv"),
        Path("/mnt/media/downloads/_done/Some.Movie.2023.1080p.BluRay.mkv"),
        Path("/mnt/media/downloads/_done/Breaking.Bad.S01E01.720p.mkv"),
    ]

    for filepath in test_files:
        print(f"\n📁 Checking: {filepath.name}")

        # Parse filename
        parsed = parse_media_filename(filepath.name)
        if not parsed:
            print("   ❌ Could not parse filename")
            continue

        title, year, content_type = parsed
        print(f"   Title: {title}")
        print(f"   Year: {year}")
        print(f"   Type: {content_type}")

        # Check if exists in library
        exists = check_episode_in_library(radarr, sonarr, filepath, logger)

        if exists:
            print(f"   ✅ FOUND in library (would be deleted)")
        else:
            print(f"   ❌ NOT FOUND in library (would be kept)")


def test_episode_parsing():
    """Test season/episode parsing from filenames."""
    print("\n")
    print("=" * 60)
    print("TESTING SEASON/EPISODE PARSING")
    print("=" * 60)

    import re

    test_cases = [
        "My Hero Academia S08E08 VOSTFR 1080p WEB x264 AAC -Tsundere-Raws (CR).mkv",
        "Breaking.Bad.S01E01.720p.BluRay.x264.mkv",
        "Avatar.The.Last.Airbender.1x01.720p.mkv",
        "Stranger Things s04e09 1080p.mkv",
        "Game.of.Thrones.3x10.HDTV.mkv",
    ]

    for filename in test_cases:
        print(f"\n📁 {filename}")

        # Try S##E## format
        match = re.search(r'[Ss](\d{1,2})[Ee](\d{1,2})', filename)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            print(f"   ✅ S{season:02d}E{episode:02d} (SxxExx format)")
            continue

        # Try #x# format
        match = re.search(r'(\d{1,2})x(\d{1,2})', filename)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            print(f"   ✅ {season}x{episode:02d} (NxNN format)")
            continue

        print(f"   ❌ No season/episode pattern found")


if __name__ == '__main__':
    print("\nFallback Detection Test Suite")
    print("=" * 60)

    # Test 1: Filename parsing
    test_filename_parsing()

    # Test 2: Season/episode parsing
    test_episode_parsing()

    # Test 3: Fallback detection (requires API access)
    print("\n\n⚠️  The next test requires valid API credentials in config.yaml")
    input("Press Enter to continue or Ctrl+C to exit...")
    test_fallback_detection()

    print("\n" + "=" * 60)
    print("✅ ALL TESTS COMPLETED")
    print("=" * 60)
