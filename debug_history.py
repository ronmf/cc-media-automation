#!/usr/bin/env python3
"""Debug script to check Sonarr import history for My Hero Academia."""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from utils.config_loader import load_config
from utils.api_clients import SonarrAPI

# Load configuration
config = load_config('config.yaml')

# Initialize Sonarr API
sonarr = SonarrAPI(
    config['sonarr']['url'],
    config['sonarr']['api_key']
)

print("Fetching Sonarr import history...")
print("=" * 80)

# Get history
history = sonarr._request('GET', '/api/v3/history', params={'eventType': 3, 'pageSize': 10000})

if history and 'records' in history:
    print(f"Total import records: {len(history['records'])}")
    print()

    # Search for My Hero Academia
    print("Searching for 'My Hero Academia' entries...")
    print("=" * 80)

    found = 0
    for record in history['records']:
        series_title = record.get('series', {}).get('title', '')
        source_title = record.get('sourceTitle', '')
        dropped_path = record.get('data', {}).get('droppedPath', '')

        if 'My Hero Academia' in series_title or 'My Hero Academia' in source_title or 'My Hero Academia' in dropped_path:
            found += 1
            print(f"\n--- Import #{found} ---")
            print(f"Series: {series_title}")
            print(f"Source Title: {source_title}")
            print(f"Dropped Path: {dropped_path}")

            # Extract filename from droppedPath
            if dropped_path:
                filename = Path(dropped_path).name
                print(f"Extracted Filename: {filename}")

            # Show episode info
            episode = record.get('episode', {})
            if episode:
                season = episode.get('seasonNumber', 'N/A')
                ep_num = episode.get('episodeNumber', 'N/A')
                print(f"Episode: S{season:02d}E{ep_num:02d}")

    print()
    print("=" * 80)
    print(f"Found {found} 'My Hero Academia' import records")

    # Check specifically for S08E08
    print()
    print("Checking specifically for S08E08...")
    print("=" * 80)

    for record in history['records']:
        episode = record.get('episode', {})
        if episode and episode.get('seasonNumber') == 8 and episode.get('episodeNumber') == 8:
            series_title = record.get('series', {}).get('title', '')
            if 'My Hero Academia' in series_title:
                print(f"\n✓ FOUND S08E08:")
                print(f"  Series: {series_title}")
                print(f"  Source Title: {record.get('sourceTitle', '')}")
                print(f"  Dropped Path: {record.get('data', {}).get('droppedPath', '')}")

                dropped_path = record.get('data', {}).get('droppedPath', '')
                if dropped_path:
                    filename = Path(dropped_path).name
                    print(f"  Filename: {filename}")

                    # Check if it matches the file in _done
                    target_file = "My Hero Academia S08E08 VOSTFR 1080p WEB x264 AAC -Tsundere-Raws (CR).mkv"
                    if filename == target_file:
                        print(f"  ✓✓✓ EXACT MATCH with file in _done!")
                    else:
                        print(f"  ✗✗✗ NO MATCH with file in _done")
                        print(f"      Expected: {target_file}")
                        print(f"      Got:      {filename}")

else:
    print("No history records found!")
