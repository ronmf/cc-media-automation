#!/usr/bin/env python3
"""Check if My Hero Academia S08E08 exists in Sonarr library."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config_loader import load_config
from utils.api_clients import SonarrAPI

# Load configuration
config = load_config('config.yaml')

# Initialize Sonarr API
sonarr = SonarrAPI(config['sonarr']['url'], config['sonarr']['api_key'])

print("Searching for My Hero Academia in Sonarr...")
print("=" * 80)

# Get all series
series_list = sonarr._request('GET', '/api/v3/series')

# Find My Hero Academia
mha_series = None
for series in series_list:
    if 'My Hero Academia' in series.get('title', ''):
        mha_series = series
        print(f"Found series: {series['title']}")
        print(f"Series ID: {series['id']}")
        print(f"TVDB ID: {series.get('tvdbId', 'N/A')}")
        print()
        break

if not mha_series:
    print("My Hero Academia not found in Sonarr!")
    sys.exit(1)

# Get episodes for this series
print("Fetching episodes...")
print("=" * 80)

episodes = sonarr._request('GET', f"/api/v3/episode?seriesId={mha_series['id']}")

# Find Season 8
season_8_episodes = [ep for ep in episodes if ep.get('seasonNumber') == 8]

print(f"Found {len(season_8_episodes)} episodes in Season 8")
print()

# Check S08E08 specifically
s08e08 = None
for ep in season_8_episodes:
    if ep.get('episodeNumber') == 8:
        s08e08 = ep
        break

if s08e08:
    print("✓ S08E08 FOUND IN SONARR!")
    print("=" * 80)
    print(f"Title: {s08e08.get('title', 'N/A')}")
    print(f"Air Date: {s08e08.get('airDate', 'N/A')}")
    print(f"Has File: {s08e08.get('hasFile', False)}")

    if s08e08.get('hasFile'):
        # Get episode file details
        if 'episodeFileId' in s08e08:
            file_id = s08e08['episodeFileId']
            print(f"Episode File ID: {file_id}")

            # Fetch full file details
            episode_file = sonarr._request('GET', f"/api/v3/episodefile/{file_id}")
            if episode_file:
                print(f"File Path: {episode_file.get('path', 'N/A')}")
                print(f"File Size: {episode_file.get('size', 0) / (1024**3):.2f} GB")
                print(f"Quality: {episode_file.get('quality', {}).get('quality', {}).get('name', 'N/A')}")
    else:
        print("✗ NO FILE ATTACHED")
        print("The episode exists in Sonarr but has not been imported yet!")
        print()
        print("This explains why it's not in the import history and not being cleaned up.")
else:
    print("✗ S08E08 NOT FOUND")
    print("Available episodes in Season 8:")
    for ep in sorted(season_8_episodes, key=lambda x: x.get('episodeNumber', 0)):
        ep_num = ep.get('episodeNumber')
        has_file = "✓" if ep.get('hasFile') else "✗"
        print(f"  {has_file} E{ep_num:02d}: {ep.get('title', 'N/A')}")
