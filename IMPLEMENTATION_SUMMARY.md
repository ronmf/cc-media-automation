# Implementation Summary: Fallback Detection for Manual Imports

**Date**: 2025-11-30
**Feature**: Phase 3 Fallback Detection for Manually Imported Files
**Status**: ✅ COMPLETED

---

## Problem

Files manually imported to Radarr/Sonarr (without history records) were never deleted from the `_done` staging directory, causing disk space issues.

**Example**:
- File: `My Hero Academia S08E08 VOSTFR 1080p WEB x264 AAC -Tsundere-Raws (CR).mkv`
- Exists in: `/mnt/media/downloads/_done/` (staging)
- Also exists in: `/media/series/My Hero Academia/Season 8/` (library, renamed)
- History: NO RECORD (manual import)
- Old behavior: File kept forever in _done
- New behavior: File detected and deleted from _done

---

## Solution

Implemented **two-tier detection system** in Phase 3 of `seedbox_purge.py`:

### Tier 1: History-Based Detection (Primary)
- Fast O(1) lookup in import history API
- Matches by original filename (`droppedPath`)
- Works for all automated imports

### Tier 2: Fallback Detection (Secondary)
- Parses filename to extract metadata
- Queries Radarr/Sonarr to check if episode/movie exists
- Verifies `hasFile=true` in library
- Catches manual imports without history

---

## Files Modified

### 1. `/home/ronz0/Apps/cc-home/cc-media-automation/scripts/seedbox_purge.py`

**New function** (lines 1072-1150):
```python
def check_episode_in_library(radarr: RadarrAPI, sonarr: SonarrAPI, filepath: Path, logger) -> bool:
    """Check if a file's episode/movie exists in library with a file attached."""
```

**Updated function** (lines 1153-1198):
```python
def purge_local_done(
    config: dict,
    library_files: Set[str],
    imported_done_files: Set[str],
    radarr: RadarrAPI,  # NEW PARAMETER
    sonarr: SonarrAPI,  # NEW PARAMETER
    logger,
    dry_run: bool = False,
    verbose: bool = False
) -> Tuple[int, int]:
```

**Detection logic** (lines 1224-1254):
```python
# Primary: Check import history
was_imported_history = filename in imported_done_files

# Fallback: Parse filename and check library
was_imported_library = False
if not was_imported_history and classification in ('video', 'subtitle'):
    was_imported_library = check_episode_in_library(radarr, sonarr, item, logger)

was_imported = was_imported_history or was_imported_library

# Decision
if classification == 'extra':
    reason = "extra file (trailer/sample/txt/nfo)"
    should_delete = True
elif was_imported:
    if was_imported_history:
        reason = "imported to library (confirmed via history)"
    else:
        reason = "episode/movie exists in library (manual import detected)"
    should_delete = True
else:
    # Keep file (not imported yet)
    continue
```

**Updated call site** (line 1418):
```python
deleted, size_freed = purge_local_done(
    config, library_files, imported_done_files, radarr, sonarr, logger,  # Added radarr, sonarr
    dry_run=args.dry_run, verbose=args.verbose
)
```

**Updated help text** (lines 1501-1505):
```
Phase 3: Local _done Files (Filesystem)
  - Clean up staging directory after import
  - Delete files confirmed imported via history
  - Fallback: Parse filenames and check if episode/movie exists in library
  - Catches manual imports without history records
```

---

### 2. `/home/ronz0/Apps/cc-home/cc-media-automation/CLAUDE.md`

**Updated documentation** (lines 91-97):
```markdown
- **Phase 3 (Filesystem)**: ✨ Purge local _done staging files with fallback detection
  - **Primary detection**: Uses Radarr/Sonarr import history (matches by original filename)
  - **Fallback detection**: Parses filenames and checks if episode/movie exists in library
  - Catches manual imports without history records (e.g., My Hero Academia S08E08)
  - Parses S##E## or #x# patterns, checks Sonarr for hasFile=true
  - Same logic for movies using title + year matching in Radarr
  - Deletes confirmed imports and extras, keeps pending files
```

---

## New Files Created

### 1. `/home/ronz0/Apps/cc-home/cc-media-automation/tests/test_fallback_detection.py`

**Purpose**: Test suite for fallback detection

**Features**:
- Test filename parsing (`parse_media_filename()`)
- Test season/episode extraction (S##E##, #x#)
- Test library lookups with live API calls
- Examples for both series and movies

**Usage**:
```bash
cd /home/ronz0/Apps/cc-home/cc-media-automation
python3 tests/test_fallback_detection.py
```

---

### 2. `/home/ronz0/Apps/cc-home/cc-media-automation/docs/FALLBACK_DETECTION.md`

**Purpose**: Comprehensive technical documentation

**Contents**:
- Problem statement with example
- How it works (algorithm walkthrough)
- Code implementation details
- Supported filename patterns
- Performance considerations
- Testing procedures
- Troubleshooting guide
- Future enhancements

---

## How It Works

### For Series (Example: My Hero Academia S08E08)

1. **Parse filename**:
   ```
   Input:  "My Hero Academia S08E08 VOSTFR 1080p WEB x264 AAC -Tsundere-Raws (CR).mkv"
   Output: title="My Hero Academia", season=8, episode=8
   ```

2. **Search Sonarr**:
   ```python
   series_list = sonarr.get_series()
   for series in series_list:
       if "my hero academia" in series['title'].lower():
           # Found: "My Hero Academia (Dub)" or "My Hero Academia"
   ```

3. **Check episode**:
   ```python
   episodes = sonarr.get_episodes(series_id)
   for ep in episodes:
       if ep['seasonNumber'] == 8 and ep['episodeNumber'] == 8:
           if ep['hasFile']:
               return True  # Episode exists in library
   ```

### For Movies (Example: The Lion King 1994)

1. **Parse filename**:
   ```
   Input:  "The.Lion.King.1994.1080p.BluRay.mkv"
   Output: title="The Lion King", year=1994, content_type="movie"
   ```

2. **Search Radarr**:
   ```python
   movies = radarr.get_movies()
   for movie in movies:
       if "lion king" in movie['title'].lower():
           if movie['year'] == 1994 and movie['hasFile']:
               return True  # Movie exists in library
   ```

---

## Supported Patterns

### Series Patterns

| Format | Example | Extracted |
|--------|---------|-----------|
| SxxExx | `Breaking.Bad.S01E01.mkv` | Season 1, Episode 1 |
| sXXeXX | `game.of.thrones.s04e09.mkv` | Season 4, Episode 9 |
| NxNN | `Avatar.1x01.720p.mkv` | Season 1, Episode 1 |

### Movie Patterns

- Extracts title, year, and content type
- Removes quality tags (1080p, BluRay, WEB-DL)
- Removes release groups (-SPARKS, [RELEASE])

---

## Testing

### Manual Test Procedure

1. **Create test file**:
   ```bash
   touch "/mnt/media/downloads/_done/My Hero Academia S08E08 Test.mkv"
   ```

2. **Run dry-run**:
   ```bash
   cd /home/ronz0/Apps/cc-home/cc-media-automation
   python3 scripts/seedbox_purge.py --dry-run --verbose \
       --skip-auto-import --skip-torrents --skip-remote-files
   ```

3. **Expected output**:
   ```
   🗑️  [DRY-RUN] Would delete local: My Hero Academia S08E08 Test.mkv (0.00 GB) - episode/movie exists in library (manual import detected)
   ```

4. **Check logs**:
   ```bash
   grep "manual import detected" logs/seedbox_purge.log
   ```

### Automated Test

```bash
python3 tests/test_fallback_detection.py
```

---

## Performance Impact

### When Fallback Runs
- **Only for files without history**: Skipped if history record exists
- **Only for videos/subtitles**: Extras are always deleted
- **Per-file basis**: Each file checked independently

### API Calls
- **Tier 1**: 2 API calls total (Radarr + Sonarr history)
- **Tier 2**: Up to 4 API calls per file:
  - `GET /api/v3/series` (Sonarr)
  - `GET /api/v3/episode?seriesId=X` (Sonarr)
  - `GET /api/v3/movie` (Radarr)
  - Check `hasFile` flag

### Optimization
- Early exit after first match
- Fuzzy title matching (case-insensitive)
- Debug logging (avoid verbose output)

---

## Safety Features

1. **Dry-run mode**: Test before executing
2. **Verbose logging**: See all detection decisions
3. **History priority**: Fallback only runs if history check fails
4. **File type filtering**: Only videos/subtitles use fallback
5. **Multiple checks**: Title + season + episode + hasFile

---

## Usage Examples

### Full cleanup (all phases)
```bash
python3 scripts/seedbox_purge.py --dry-run --verbose
python3 scripts/seedbox_purge.py --execute
```

### Phase 3 only (local _done cleanup)
```bash
python3 scripts/seedbox_purge.py --dry-run --verbose \
    --skip-auto-import --skip-torrents --skip-remote-files
```

### Skip Phase 3 (disable fallback detection)
```bash
python3 scripts/seedbox_purge.py --execute --skip-local-done
```

---

## Future Enhancements

1. **Caching**: Cache series/movie lists for entire run
2. **Better matching**: Levenshtein distance for fuzzy matching
3. **Configuration**: Enable/disable fallback via config.yaml
4. **Performance**: Parallel API calls, database cache
5. **Reporting**: Show fallback detection statistics

---

## Troubleshooting

### File not detected as imported

**Check filename parsing**:
```bash
python3 -c "from scripts.seedbox_purge import parse_media_filename; print(parse_media_filename('YOUR_FILE.mkv'))"
```

**Check series title in Sonarr**:
```bash
curl -H "X-Api-Key: YOUR_KEY" http://localhost:8989/api/v3/series | jq '.[].title'
```

**Enable debug logging**:
```yaml
# config.yaml
logging:
  level: DEBUG
```

### Performance slow

**Solutions**:
1. Cache API responses outside loop
2. Batch process files by series
3. Disable fallback if too slow

---

## Related Documentation

- [CLAUDE.md](CLAUDE.md) - Project overview
- [SERVARR_AUTOMATION_PLAN_V2.md](SERVARR_AUTOMATION_PLAN_V2.md) - Full automation plan
- [docs/FALLBACK_DETECTION.md](docs/FALLBACK_DETECTION.md) - Detailed technical docs
- [tests/test_fallback_detection.py](tests/test_fallback_detection.py) - Test suite

---

## Summary

**Lines of code**: ~150 new lines
**Files modified**: 2 (seedbox_purge.py, CLAUDE.md)
**Files created**: 3 (test script, technical docs, this summary)
**Testing**: Manual test procedure + automated test script
**Safety**: Dry-run mode, verbose logging, multiple checks
**Performance**: Minimal impact (fallback only for files without history)

**Status**: ✅ Ready for production testing
