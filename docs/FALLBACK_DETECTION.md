# Fallback Detection for Manual Imports

## Problem Statement

When files are manually imported to Radarr/Sonarr (via the UI or other means), they may not have history records in the import history API. This causes Phase 3 of `seedbox_purge.py` to keep these files in the `_done` directory indefinitely, even though they've already been imported to the library.

### Example Scenario

**File**: `My Hero Academia S08E08 VOSTFR 1080p WEB x264 AAC -Tsundere-Raws (CR).mkv`

**Locations**:
1. **_done folder**: `/mnt/media/downloads/_done/` (original file)
2. **Sonarr library**: `/media/series/My Hero Academia/Season 8/` (renamed file)
3. **Import history**: NO RECORD (manual import or history purged)

**Old behavior**: File kept in _done forever (no history match)
**New behavior**: File deleted from _done (detected via filename parsing + library check)

---

## Solution: Two-Tier Detection System

Phase 3 now uses a **two-tier detection system**:

### Tier 1: History-Based Detection (Primary)
- Check Radarr/Sonarr import history API
- Match by original filename using `droppedPath` field
- Fast and accurate (O(1) lookup)
- Works for all automated imports

### Tier 2: Fallback Detection (Secondary)
- Parse filename to extract metadata (title, season, episode)
- Query Radarr/Sonarr APIs to check if episode/movie exists
- Check `hasFile=true` to confirm file is in library
- Slower (O(n) search) but catches manual imports

---

## How It Works

### For Series

1. **Parse filename** to extract season/episode:
   ```python
   # Input: My Hero Academia S08E08 VOSTFR 1080p WEB x264 AAC -Tsundere-Raws (CR).mkv
   # Output: title="My Hero Academia", season=8, episode=8
   ```

2. **Search Sonarr** for matching series:
   ```python
   # Search by title (fuzzy match)
   series_list = sonarr.get_series()
   for series in series_list:
       if "my hero academia" in series['title'].lower():
           # Found series
   ```

3. **Check if episode has file**:
   ```python
   episodes = sonarr.get_episodes(series_id)
   for ep in episodes:
       if ep['seasonNumber'] == 8 and ep['episodeNumber'] == 8:
           if ep['hasFile']:
               return True  # File exists in library
   ```

### For Movies

1. **Parse filename** to extract title and year:
   ```python
   # Input: The.Lion.King.1994.1080p.BluRay.mkv
   # Output: title="The Lion King", year=1994, content_type="movie"
   ```

2. **Search Radarr** for matching movie:
   ```python
   movies = radarr.get_movies()
   for movie in movies:
       if "lion king" in movie['title'].lower():
           if movie['year'] == 1994 and movie['hasFile']:
               return True  # Movie exists in library
   ```

---

## Code Implementation

### New Function: `check_episode_in_library()`

Located in: `scripts/seedbox_purge.py`

```python
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
```

### Updated Function: `purge_local_done()`

**New parameters**:
- `radarr: RadarrAPI` - For movie lookups
- `sonarr: SonarrAPI` - For series/episode lookups

**Detection logic**:
```python
# Check if this file was imported from _done (via history)
filename = item.name
was_imported_history = filename in imported_done_files

# Fallback: Check if episode/movie exists in library (for manual imports)
was_imported_library = False
if not was_imported_history and classification in ('video', 'subtitle'):
    was_imported_library = check_episode_in_library(radarr, sonarr, item, logger)

was_imported = was_imported_history or was_imported_library

# Decision logic:
if classification == 'extra':
    should_delete = True
    reason = "extra file (trailer/sample/txt/nfo)"
elif was_imported:
    should_delete = True
    if was_imported_history:
        reason = "imported to library (confirmed via history)"
    else:
        reason = "episode/movie exists in library (manual import detected)"
else:
    # Keep file (not imported yet)
    continue
```

---

## Supported Filename Patterns

### Series Patterns

| Pattern | Example | Extracted |
|---------|---------|-----------|
| `SxxExx` | `Breaking.Bad.S01E01.mkv` | S01E01 |
| `sXXeXX` | `game.of.thrones.s04e09.mkv` | S04E09 |
| `NxNN` | `Avatar.1x01.720p.mkv` | 1x01 |
| `NxN` | `Friends.10x1.mkv` | 10x01 |

### Movie Patterns

- Title extraction: Removes quality tags, release groups, year
- Year detection: `(19\d{2}|20\d{2})` - 1900-2099
- Examples:
  - `The.Lion.King.1994.1080p.BluRay.mkv` → "The Lion King", 1994
  - `Inception.2010.720p.WEB-DL.mkv` → "Inception", 2010

---

## Performance Considerations

### When Fallback Runs
- **Only for files without history**: If history record exists, fallback is skipped
- **Only for videos/subtitles**: Extras are always deleted, no fallback needed
- **Per-file basis**: Each file checked independently

### API Calls
- **Tier 1 (history)**: 2 API calls total (Radarr + Sonarr history)
- **Tier 2 (fallback)**: Up to 4 API calls per file:
  1. `GET /api/v3/series` (Sonarr) - list all series
  2. `GET /api/v3/episode?seriesId=X` - get episodes for matched series
  3. `GET /api/v3/movie` (Radarr) - list all movies
  4. Check `hasFile` flag

### Optimization Strategies
- **Cache API responses**: Series/movie lists fetched once per run
- **Early exit**: Stop searching after first match
- **Fuzzy matching**: "my hero academia" matches "My Hero Academia (Dub)"
- **Debug logging**: Use `logger.debug()` to avoid verbose output

---

## Testing

### Test Script
Location: `tests/test_fallback_detection.py`

```bash
# Test filename parsing
python3 tests/test_fallback_detection.py

# Run full test with API access
cd /home/ronz0/Apps/cc-home/cc-media-automation
python3 tests/test_fallback_detection.py
```

### Manual Testing

1. **Create test file** in _done:
   ```bash
   touch "/mnt/media/downloads/_done/My Hero Academia S08E08 Test.mkv"
   ```

2. **Run Phase 3** in dry-run mode:
   ```bash
   python3 scripts/seedbox_purge.py --dry-run --verbose \
       --skip-auto-import --skip-torrents --skip-remote-files
   ```

3. **Check logs** for detection:
   ```bash
   grep "manual import detected" logs/seedbox_purge.log
   ```

Expected output:
```
🗑️  [DRY-RUN] Would delete local: My Hero Academia S08E08 Test.mkv (0.00 GB) - episode/movie exists in library (manual import detected)
```

---

## Troubleshooting

### Issue: File not detected as imported

**Symptoms**:
- File kept in _done even though episode exists in library
- Log shows: `KEEP (not imported yet)`

**Causes**:
1. **Filename unparseable**: Missing S##E## or #x# pattern
2. **Title mismatch**: Parsed title doesn't match Sonarr series title
3. **Episode doesn't exist**: Series exists but episode not imported
4. **No file attached**: Episode exists but `hasFile=false`

**Solutions**:
1. Check filename format:
   ```bash
   python3 -c "from scripts.seedbox_purge import parse_media_filename; print(parse_media_filename('YOUR_FILE.mkv'))"
   ```

2. Check series title in Sonarr:
   ```bash
   curl -H "X-Api-Key: YOUR_KEY" http://localhost:8989/api/v3/series | jq '.[].title'
   ```

3. Enable debug logging:
   ```yaml
   # config.yaml
   logging:
     level: DEBUG  # Shows all API calls and matches
   ```

### Issue: Wrong file deleted

**Symptoms**:
- File deleted but episode doesn't actually exist in library
- False positive match

**Causes**:
1. **Fuzzy matching too broad**: "Avatar" matches "Avatar 2009" and "Avatar: The Last Airbender"
2. **Year mismatch**: Movie year doesn't match parsed year

**Solutions**:
1. Improve title matching logic (add word boundaries)
2. Require exact year match for movies
3. Add whitelist/blacklist patterns in config

### Issue: Performance slow

**Symptoms**:
- Phase 3 takes long time to complete
- Many API calls in logs

**Causes**:
- Many files without history records
- Large library (1000+ series/movies)

**Solutions**:
1. **Cache API responses**:
   ```python
   # Fetch once, reuse for all files
   all_series = sonarr.get_series()  # Outside loop
   all_movies = radarr.get_movies()  # Outside loop
   ```

2. **Batch processing**: Group files by series before API calls

3. **Skip fallback**: If too slow, disable with flag
   ```yaml
   # config.yaml
   thresholds:
     enable_fallback_detection: false  # Future enhancement
   ```

---

## Future Enhancements

### 1. Caching
- Cache series/movie lists for entire run
- Avoid repeated API calls for same series

### 2. Better Title Matching
- Use Levenshtein distance for fuzzy matching
- Handle special characters and accents
- Match alternative titles (aliases)

### 3. Configuration Options
```yaml
# config.yaml
thresholds:
  enable_fallback_detection: true  # Enable/disable fallback
  fallback_fuzzy_threshold: 0.8     # Similarity threshold (0.0-1.0)
  fallback_max_api_calls: 100       # Limit API calls per run
```

### 4. Performance Optimization
- Parallel API calls (ThreadPoolExecutor)
- Database cache for lookups
- Incremental processing (resume from last position)

### 5. Reporting
```
Phase 3 Summary:
  Files deleted: 50
    - Via history: 45 (90%)
    - Via fallback: 5 (10%)
    - Extras: 10 (20%)
  Files kept: 5
  API calls made: 25
```

---

## See Also

- [SERVARR_AUTOMATION_PLAN_V2.md](../SERVARR_AUTOMATION_PLAN_V2.md) - Full automation plan
- [seedbox_purge.py](../scripts/seedbox_purge.py) - Implementation
- [Radarr API Docs](https://radarr.video/docs/api/) - API reference
- [Sonarr API Docs](https://sonarr.tv/docs/api/) - API reference
