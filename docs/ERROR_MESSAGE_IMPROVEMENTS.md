# Error Message Improvements

## Overview

The API clients in `utils/api_clients.py` now provide human-readable error messages instead of raw JSON dumps when Radarr/Sonarr API requests fail.

## Changes Made

### 1. New Validation Error Parser

Added `parse_validation_errors()` function that converts Radarr/Sonarr validation error responses into clear, actionable messages.

**Location**: `utils/api_clients.py` (lines 24-74)

**Handles These Error Types**:
- `MovieExistsValidator` - Duplicate movie detection
- `SeriesExistsValidator` - Duplicate series detection
- `RootFolderValidator` - Invalid root folder paths
- `QualityProfileValidator` - Invalid quality profile IDs
- `PathValidator` - Invalid file paths
- Generic validation errors with custom messages

### 2. Improved Error Logging

Updated the `BaseAPI._request()` method to:
- Use the parser for 400 Bad Request errors
- Remove verbose "attempt X/Y" messages from warnings
- Show concise, clear error details
- Maintain full context for debugging

**Location**: `utils/api_clients.py` (lines 163-189)

## Before and After Examples

### Example 1: Duplicate Movie

**Before (Raw JSON)**:
```
Response: [{'propertyName': 'TmdbId', 'errorMessage': 'This movie has already been added', 'attemptedValue': 550, 'severity': 'error', 'errorCode': 'MovieExistsValidator', 'formattedMessageArguments': [], 'formattedMessagePlaceholderValues': {}}]
HTTP error (attempt 3/3): 400 Client Error: Bad Request for url: http://localhost:7878/api/v3/movie
```

**After (Human-Readable)**:
```
400 Bad Request:
  URL: http://localhost:7878/api/v3/movie
  Method: POST
  Request JSON: {...}
  Validation failed: Movie already exists (TMDB ID: 550)
HTTP error: 400 Client Error: Bad Request for url: http://localhost:7878/api/v3/movie
```

### Example 2: Duplicate Series

**Before (Raw JSON)**:
```
Response: [{'propertyName': 'TvdbId', 'errorMessage': 'This series has already been added', 'attemptedValue': 305074, 'severity': 'error', 'errorCode': 'SeriesExistsValidator', ...}]
HTTP error (attempt 3/3): 400 Client Error: Bad Request for url: http://localhost:8989/api/v3/series
```

**After (Human-Readable)**:
```
400 Bad Request:
  URL: http://localhost:8989/api/v3/series
  Method: POST
  Request JSON: {...}
  Validation failed: Series already exists (TVDB ID: 305074)
HTTP error: 400 Client Error: Bad Request for url: http://localhost:8989/api/v3/series
```

### Example 3: Invalid Root Folder

**Before (Raw JSON)**:
```
Response: [{'propertyName': 'RootFolderPath', 'errorMessage': 'Root folder path is invalid', 'attemptedValue': '/mnt/media/movies_invalid', ...}]
```

**After (Human-Readable)**:
```
Validation failed: Invalid root folder path: /mnt/media/movies_invalid
```

### Example 4: Invalid Quality Profile

**Before (Raw JSON)**:
```
Response: [{'propertyName': 'QualityProfileId', 'errorMessage': 'Quality profile does not exist', 'attemptedValue': 999, ...}]
```

**After (Human-Readable)**:
```
Validation failed: Invalid quality profile: 999
```

### Example 5: Multiple Validation Errors

**Before (Raw JSON)**:
```
Response: [{'propertyName': 'RootFolderPath', 'errorMessage': 'Root folder path is invalid', ...}, {'propertyName': 'QualityProfileId', 'errorMessage': 'Quality profile does not exist', ...}]
```

**After (Human-Readable)**:
```
Validation failed: Invalid root folder path: /invalid/path; Invalid quality profile: 999
```

## Benefits

### For Users
- **Clear Error Messages**: Immediately understand what went wrong
- **Actionable Information**: Know exactly what needs to be fixed
- **Less Noise**: Removed verbose retry attempt counters
- **Consistent Format**: All errors follow the same pattern

### For Debugging
- **Maintained Context**: Full URL, method, and request body still logged
- **Structured Output**: Easy to parse and search in logs
- **Error Classification**: Quickly identify error types

### For Automation
- **Better Log Parsing**: Consistent error format for monitoring
- **Duplicate Detection**: Easily identify "already exists" errors
- **Configuration Validation**: Clear feedback on invalid settings

## Usage in Scripts

Scripts using `RadarrAPI.add_movie()` or `SonarrAPI.add_series()` automatically benefit from these improvements.

**Example from `seedbox_purge.py`**:

```python
try:
    radarr.add_movie(
        tmdb_id=tmdb_id,
        title=title,
        year=year,
        quality_profile_id=quality_id,
        root_folder_path=root_folder,
        monitored=True,
        search_on_add=True
    )
    logger.info(f"Successfully imported: {title} ({year})")
except APIError as e:
    # Error message now shows: "Movie already exists (TMDB ID: 550)"
    # Instead of raw JSON dump
    logger.error(f"Failed to import {title}: {e}")
```

## Testing

Run the test script to see all error message formats:

```bash
cd /home/ronz0/Apps/cc-home/cc-media-automation
python3 test_error_messages.py
```

**Expected Output**:
```
Testing Validation Error Parser
============================================================

Test 1: Movie Already Exists
------------------------------------------------------------
Parsed: Movie already exists (TMDB ID: 550)

Test 2: Series Already Exists
------------------------------------------------------------
Parsed: Series already exists (TVDB ID: 305074)

Test 3: Invalid Root Folder
------------------------------------------------------------
Parsed: Invalid root folder path: /invalid/path

[... etc ...]
```

## Common Error Messages

Here are the most common error messages you'll see:

| Error Type | Message Format | Cause |
|------------|----------------|-------|
| Duplicate Movie | `Movie already exists (TMDB ID: 12345)` | Movie already in Radarr |
| Duplicate Series | `Series already exists (TVDB ID: 67890)` | Series already in Sonarr |
| Invalid Root Folder | `Invalid root folder path: /path/to/folder` | Root folder doesn't exist or not configured |
| Invalid Quality Profile | `Invalid quality profile: 999` | Quality profile ID not found |
| Invalid Path | `Invalid path: /invalid/path` | Path contains invalid characters |
| Missing Required Field | `Title is required` | Required field not provided |

## Implementation Details

### Parser Function Signature

```python
def parse_validation_errors(response_json: Any) -> str:
    """Parse Radarr/Sonarr validation error responses into human-readable messages.

    Args:
        response_json: List of validation error dicts from API, or other response data

    Returns:
        Human-readable error message
    """
```

### Error Code Mapping

| Error Code Pattern | Output Format |
|-------------------|---------------|
| `MovieExistsValidator` | `Movie already exists (TMDB ID: {value})` |
| `SeriesExistsValidator` | `Series already exists (TVDB ID: {value})` |
| `RootFolderValidator` | `Invalid root folder path: {value}` |
| `QualityProfileValidator` | `Invalid quality profile: {value}` |
| `PathValidator` | `Invalid path: {value}` |
| Other with message | `{errorMessage} ({propertyName}: {value})` |
| Other without message | `Validation error: {errorCode}` |

### Fallback Behavior

If the response is not a list of validation errors (e.g., plain text error), the parser returns the raw response as a string. This ensures compatibility with all API error responses.

## Files Modified

- `/home/ronz0/Apps/cc-home/cc-media-automation/utils/api_clients.py`
  - Added `parse_validation_errors()` function
  - Updated `BaseAPI._request()` error handling
  - Improved logging clarity

## Related Documentation

- [API Quick Reference](../CLAUDE.md#api-quick-reference) - API client usage examples
- [Troubleshooting](../CLAUDE.md#troubleshooting) - Common issues and solutions
- [Test Script](../test_error_messages.py) - Comprehensive error message tests

## Future Improvements

Potential enhancements for consideration:

1. **Error Code Registry**: Centralized mapping of all known error codes
2. **Localization**: Support for non-English error messages
3. **Severity Levels**: Parse error severity (error, warning, info)
4. **Structured Logging**: JSON-formatted error logs for better parsing
5. **Error Recovery**: Automatic retry with corrections for common errors
6. **Metrics**: Track error frequencies for monitoring

---

**Last Updated**: 2025-11-30
**Version**: 1.0
