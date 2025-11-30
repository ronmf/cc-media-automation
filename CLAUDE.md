# Servarr Media Manager - Project Configuration

**Project**: Servarr Automation Suite
**Location**: `/mnt/media/scripts` (NAS: `smb://nas.home/media/scripts`)
**Agent**: `media-manager-agent` (use for all media management tasks)

---

## Project Overview

Automated media server management suite for Servarr stack with seedbox integration, library optimization, and Jellyfin notifications.

### Infrastructure

**LXC Container**: `servarr` (Proxmox)
- **OS**: Linux
- **Mount**: `/mnt/media` (SMB from NAS)
- **Execution**: Scripts run on LXC host (not inside Docker)

**Docker Services**:
| Service | Port | Purpose |
|---------|------|---------|
| Radarr | 7878 | Movie management |
| Sonarr | 8989 | Series management |
| Prowlarr | 9696 | Indexer management |
| Bazarr | 6767 | Subtitle management |
| Lingarr | 9876 | Language management |

**External Services**:
- **Jellyfin**: `http://jellyfin.home:8096`
- **Seedbox**: `185.56.20.18:40685` (user: ronz0)
- **Notifications**: `https://ntfy.les7nains.com/topic_default`

---

## Directory Structure

```
/mnt/media/scripts/
├── CLAUDE.md                    # This file
├── config.yaml                  # All settings & credentials
├── requirements.txt             # Python dependencies
├── servarr_menu.py             # Main CLI menu
│
├── scripts/                     # Automation scripts
│   ├── seedbox_sync.py
│   ├── seedbox_purge.py
│   ├── video_cleanup.py
│   ├── jellyfin_notify.py
│   ├── library_analyzer.py
│   └── library_reducer.py
│
├── utils/                       # Shared modules
│   ├── config_loader.py
│   ├── api_clients.py          # ✨ ENHANCED: Radarr/Sonarr search/add/update methods
│   ├── tmdb_client.py          # ✨ NEW: TMDB API client for age ratings
│   ├── rtorrent_client.py       # ✨ NEW: XMLRPC client for rtorrent
│   ├── ntfy_notifier.py
│   ├── logger.py
│   ├── seedbox_ssh.py
│   └── validators.py
│
├── logs/                        # Execution logs (30-day retention)
├── backups/                     # Metadata backups
└── reports/                     # Analysis reports
```

---

## Scripts Overview

### High Priority
1. **`seedbox_sync.py`** - Download from seedbox via lftp
   - Remote: `/downloads` → Local: `/mnt/media/downloads/_done`
   - Remove source files after successful transfer
   - Clean up `*.lftp` temp files

2. **`seedbox_purge.py`** - ✨ 4-Phase: Auto-Import + Cleanup
   - **Phase 0 (Auto-Import)**: Parse unmanaged files → TMDB age rating → route to kids/adult libraries
   - **Phase 1 (XMLRPC)**: Delete torrents by hash matching (downloadId)
   - **Phase 2 (SSH)**: Clean orphaned remote /downloads files
   - **Phase 3 (Filesystem)**: Purge local _done staging files
   - Policy: ratio >= 1.5 OR age >= 2 days
   - Kids content: G, PG, TV-Y, TV-Y7, TV-G, TV-PG → kids_movies/kids_series
   - Adult content: Everything else → movies/series
   - 100% accurate hash-based matching
   - Monitor 750GB quota (warn at 700GB)
   - Flags: --skip-auto-import, --skip-torrents, --skip-remote-files, --skip-local-done

   **Note**: For legacy SSH-only file cleanup, use `seedbox_file_cleanup.py`

### Medium Priority
3. **`video_cleanup.py`** - Remove extras/trailers/samples
   - Keep only main video (> 500MB) + subtitles
   - Delete via Radarr/Sonarr APIs or filesystem
   - Don't preserve NFO/fanart (Jellyfin retrieves)

4. **`jellyfin_notify.py`** - Update Jellyfin library
   - Poll Radarr/Sonarr history
   - Trigger full library scan
   - Can be called manually or via cron

### Low Priority
5. **`library_analyzer.py`** - Watch pattern analysis
   - Score items 0-100 for deletion candidacy
   - Check Prowlarr for re-acquisition difficulty
   - Export CSV reports

6. **`library_reducer.py`** - Tag for deletion
   - Tag items in Radarr/Sonarr (don't delete directly)
   - Protect 1080p content
   - Threshold: 80%+ score

7. **`servarr_menu.py`** - Interactive CLI menu
   - Dry-run toggle (default: ON)
   - Log viewer, config display
   - "Run All" for daily tasks

---

## Configuration

**File**: `config.yaml` (single file with all settings/secrets)

**Key Settings**:
```yaml
seedbox:
  host: "185.56.20.18"
  port: 40685
  username: "ronz0"
  ssh_key: "/root/.ssh/id_rsa"
  remote_downloads: "/downloads"

paths:
  media_root: "/mnt/media"
  downloads_done: "/mnt/media/downloads/_done"
  scripts: "/mnt/media/scripts"
  logs: "/mnt/media/scripts/logs"

radarr:
  url: "http://localhost:7878"
  api_key: ""

sonarr:
  url: "http://localhost:8989"
  api_key: ""

jellyfin:
  url: "http://jellyfin.home:8096"
  api_key: ""

notifications:
  ntfy:
    url: "https://ntfy.les7nains.com/topic_default"
    token: ""  # Optional: Bearer token for authenticated topics

thresholds:
  seedbox_age_days: 2          # Torrent age threshold (OR condition)
  seedbox_min_ratio: 1.5       # Torrent ratio threshold (OR condition)
  min_video_size_mb: 500
  deletion_score_threshold: 80

safety:
  dry_run_default: true
```

---

## Common Commands

### Manual Execution
```bash
cd /mnt/media/scripts

# Interactive menu (recommended)
python3 servarr_menu.py

# Individual scripts (dry-run)
python3 scripts/seedbox_sync.py --dry-run
python3 scripts/video_cleanup.py --dry-run
python3 scripts/library_analyzer.py --dry-run

# Execute mode (CAUTION)
python3 scripts/seedbox_sync.py --execute
```

### Testing
```bash
# Test config loading
python3 -c "from utils.config_loader import load_config; print(load_config('config.yaml'))"

# Test Radarr API connection
python3 -c "from utils.api_clients import RadarrAPI; r = RadarrAPI('http://localhost:7878', 'KEY'); print(len(r.get_movies()))"

# ✨ Test rtorrent XMLRPC connection
python3 -c "
from utils.rtorrent_client import RTorrentClient
client = RTorrentClient('nl3864.dediseedbox.com', 'user', 'pass')
client.test_connection()
torrents = client.get_seeding_torrents()
print(f'Found {len(torrents)} seeding torrents')
"

# View logs
tail -f logs/seedbox_sync.log
tail -f logs/seedbox_purge.log
tail -f logs/video_cleanup.log
```

### Docker Integration
```bash
# Get Radarr API key
docker exec radarr cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+'

# View Radarr logs
docker logs radarr --tail 100

# Check media mount
docker exec radarr ls -lah /media/movies
```

---

## API Quick Reference

### ✨ TMDB API (The Movie Database)
**Endpoint**: `https://api.themoviedb.org/3`
**Auth**: API Key in query parameter
**Purpose**: Fetch age ratings and metadata for movies/TV shows

```python
from utils.tmdb_client import create_tmdb_client

tmdb = create_tmdb_client(config)

# Search for content
movies = tmdb.search_movie('The Lion King', 1994)
series = tmdb.search_tv('Avatar: The Last Airbender', 2005)

# Get age ratings
cert = tmdb.get_movie_certification('The Lion King', 1994)  # Returns: 'G'
rating = tmdb.get_tv_certification('Breaking Bad', 2008)    # Returns: 'TV-MA'

# Determine if kids content
is_kids = tmdb.is_kids_content(
    'The Lion King', 1994, 'movie',
    kids_ratings=['G', 'PG', 'TV-Y', 'TV-Y7', 'TV-G']
)  # Returns: True
```

**Key Notes**:
- Free API key from https://www.themoviedb.org/settings/api
- Rate limits: 40 requests per 10 seconds
- Certifications: US-based (MPAA for movies, TV Parental Guidelines for series)
- Returns None if no rating found

### ✨ rtorrent XMLRPC (via ruTorrent httprpc)
**Endpoint**: `https://nl3864.dediseedbox.com/rutorrent/plugins/httprpc/action.php`
**Auth**: HTTP Digest (NOT Basic!)
**Protocol**: XMLRPC (NOT JSON-RPC!)

```python
from utils.rtorrent_client import RTorrentClient

client = RTorrentClient('nl3864.dediseedbox.com', 'user', 'pass')

# Get seeding torrents (CRITICAL: empty string first param!)
seeding = client.get_seeding_torrents()

# Get torrent info
for hash_id in seeding:
    info = client.get_torrent_info(hash_id)
    print(f"{info['name']}: ratio={info['ratio']:.2f}")  # Already divided by 1000!

# Delete torrent with files
client.delete_torrent(hash_id, delete_files=True)
```

**Key Notes**:
- Empty string required: `server.download_list('', 'view')`
- Ratio auto-divided by 1000 in client
- Hash = uppercase hex (40 chars)

### Radarr (http://localhost:7878/api/v3)
```python
GET  /movie                        # List movies
GET  /movie/{id}                   # Movie details
DELETE /movie/{id}?deleteFiles=true
POST /tag                          # Create tag
PUT  /movie/{id}                   # Update (add tags)
GET  /history?eventType=grabbed    # Import history
```

### Sonarr (http://localhost:8989/api/v3)
```python
GET  /series                       # List series
GET  /episode?seriesId={id}
DELETE /series/{id}?deleteFiles=true
POST /tag
```

### Jellyfin (http://jellyfin.home:8096)
```python
GET  /Users
GET  /Users/{userId}/Items?Filters=IsUnplayed&IncludeItemTypes=Movie
POST /Library/Refresh              # Full library scan
```

**Authentication**: `X-Api-Key` header (or `X-Emby-Token` for Jellyfin)

---

## Safety Rules

1. **Dry-Run First**: Always test with `--dry-run` before `--execute`
2. **Verify Before Delete**: Check local file exists before deleting remote
3. **Lock Files**: Prevent concurrent script execution
4. **Size Verification**: Match file sizes within 1% tolerance
5. **Protected Folders**: Never delete seedbox `/_ready` folder
6. **Backup Metadata**: Save item data before deletion
7. **Tag Instead of Delete**: For library reduction, tag in Radarr/Sonarr

---

## Workflow Examples

### Daily Automation (Cron)
```bash
# Seedbox sync every 30 minutes
*/30 * * * * cd /mnt/media/scripts && python3 scripts/seedbox_sync.py --execute

# Seedbox purge daily at 3 AM
0 3 * * * cd /mnt/media/scripts && python3 scripts/seedbox_purge.py --execute

# Video cleanup weekly on Sunday at 2 AM
0 2 * * 0 cd /mnt/media/scripts && python3 scripts/video_cleanup.py --execute

# Jellyfin notify every 10 minutes
*/10 * * * * cd /mnt/media/scripts && python3 scripts/jellyfin_notify.py --execute
```

### Manual Library Optimization
```bash
# 1. Analyze library
python3 scripts/library_analyzer.py --dry-run

# 2. Review generated report
cat reports/library_analysis_$(date +%Y-%m-%d).csv

# 3. Tag items for deletion (score >= 80)
python3 scripts/library_reducer.py --report reports/library_analysis_2025-11-29.csv --execute

# 4. Review tagged items in Radarr/Sonarr UI
# 5. Manually delete via UI when satisfied
```

---

## Troubleshooting

### Seedbox Sync Issues
```bash
# Test SSH connection
ssh -p 40685 ronz0@185.56.20.18

# Check remote files
ssh -p 40685 ronz0@185.56.20.18 "ls -lah /downloads"

# Manual lftp test
lftp -p 40685 -u ronz0 sftp://185.56.20.18
```

### API Connection Issues
```bash
# Test Radarr API
curl -H "X-Api-Key: YOUR_KEY" http://localhost:7878/api/v3/system/status

# Test Jellyfin API
curl -H "X-Emby-Token: YOUR_KEY" http://jellyfin.home:8096/System/Info
```

### Log Analysis
```bash
# Find errors in logs
grep -i error logs/*.log

# Check recent activity
tail -f logs/seedbox_sync.log

# View specific date
grep "2025-11-29" logs/seedbox_sync.log
```

---

## Development Guidelines

### Code Style
- **Language**: Python 3.8+
- **Formatting**: 4 spaces, no tabs
- **Imports**: Standard lib → Third-party → Local
- **Naming**: snake_case for functions/variables
- **Comments**: Docstrings for all functions

### Script Requirements
```python
# Every script must have:
1. --dry-run / --execute flag
2. Centralized logging
3. Error handling with ntfy notifications
4. Lock file for concurrent prevention
5. Config file loading
6. Usage examples in docstring
```

### Testing Checklist
- [ ] Dry-run mode works correctly
- [ ] Logging outputs to correct location
- [ ] Config loading handles missing values
- [ ] API errors handled gracefully
- [ ] Lock file prevents concurrent runs
- [ ] ntfy notifications sent on errors
- [ ] File operations verified before execution

---

## Agent Usage

**Primary Agent**: `media-manager-agent`

Use this agent for:
- Creating/modifying automation scripts
- API integration (Radarr/Sonarr/Jellyfin)
- Seedbox sync and cleanup logic
- Library analysis algorithms
- Video file management
- CLI menu development

**Invoke with**:
```
@media-manager-agent <task description>
```

---

## Memory Keywords

- `servarr-automation` - Main automation suite
- `seedbox-sync` - lftp synchronization with cleanup
- `hash-based-purge` - ✨ XMLRPC torrent deletion by downloadId matching
- `rtorrent-xmlrpc` - ✨ HTTP Digest authentication for rtorrent API
- `library-reduction` - Watch-based deletion scoring
- `video-cleanup` - Extra/trailer removal
- `tag-deletion` - Tag-based removal workflow (not direct deletion)

---

## Quick Start

1. **First Time Setup**:
   ```bash
   cd /mnt/media/scripts
   pip3 install -r requirements.txt
   cp config.yaml.example config.yaml
   # Edit config.yaml with your API keys
   ```

2. **Test Configuration**:
   ```bash
   python3 servarr_menu.py
   # Select "8. View Config" to verify settings
   ```

3. **Run First Sync (Dry-Run)**:
   ```bash
   python3 scripts/seedbox_sync.py --dry-run
   # Review output, check logs
   ```

4. **Enable Automation**:
   ```bash
   # Add cron jobs (see Workflow Examples above)
   crontab -e
   ```

---

## Project Status

**Implementation Phase**: ✅ Complete
**Testing Phase**: ⏳ Current
**Production Deployment**: ⏳ Pending

**Completed**:
- ✅ Project structure and utilities
- ✅ config.yaml with all settings
- ✅ seedbox_sync.py (lftp-based)
- ✅ seedbox_purge.py (hash-based XMLRPC)
- ✅ rtorrent_client.py (HTTP Digest auth)
- ✅ API clients (Radarr, Sonarr, Jellyfin)
- ✅ video_cleanup.py
- ✅ jellyfin_notify.py
- ✅ library_analyzer.py
- ✅ library_reducer.py

**Next Steps**:
1. Fill in secrets in config.yaml
2. Test rtorrent XMLRPC connection
3. Test hash-based purge with dry-run
4. Deploy cron jobs
5. Monitor for 1 week
6. Adjust thresholds as needed

**Documentation**:
- [SERVARR_AUTOMATION_PLAN_V2.md](SERVARR_AUTOMATION_PLAN_V2.md) - Complete implementation guide

---

## Notes

- All scripts default to dry-run mode for safety
- Use `--execute` flag explicitly for production runs
- Logs rotate after 30 days
- Metadata backed up before any deletion
- Tag-based deletion workflow (Radarr/Sonarr handle actual deletion)
- ntfy notifications for all errors
- Single config file with all secrets (no separate secrets.json)
