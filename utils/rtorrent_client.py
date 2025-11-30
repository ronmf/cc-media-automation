"""RTorrent XMLRPC client for DediSeedbox via httprpc plugin.

This module provides a production-ready client for interacting with rtorrent
via the ruTorrent httprpc plugin using XMLRPC with HTTP Digest authentication.

Example:
    >>> from utils.rtorrent_client import RTorrentClient
    >>> client = RTorrentClient('nl3864.dediseedbox.com', 'user', 'pass')
    >>> torrents = client.get_seeding_torrents()
    >>> print(f"Found {len(torrents)} seeding torrents")
"""

import xmlrpc.client
import urllib.request
import logging
from typing import List, Dict, Any, Optional


class DigestTransport(xmlrpc.client.Transport):
    """Custom XMLRPC transport with HTTP Digest authentication.

    rtorrent/ruTorrent uses HTTP Digest auth (not Basic), so we need a
    custom transport to handle the authentication flow.
    """

    def __init__(self, username: str, password: str, use_https: bool = True):
        """Initialize Digest transport.

        Args:
            username: Seedbox username
            password: Seedbox password
            use_https: Use HTTPS (default True)
        """
        super().__init__()
        self.username = username
        self.password = password
        self.use_https = use_https
        self.verbose = False

    def request(self, host: str, handler: str, request_body: bytes, verbose: bool = False):
        """Make XMLRPC request with Digest authentication.

        Args:
            host: Server hostname
            handler: URL handler path
            request_body: XMLRPC request body
            verbose: Enable verbose output

        Returns:
            Parsed response
        """
        protocol = "https" if self.use_https else "http"
        url = f"{protocol}://{host}{handler}"

        # Create password manager
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, url, self.username, self.password)

        # Create auth handler for Digest auth
        auth_handler = urllib.request.HTTPDigestAuthHandler(password_mgr)

        # Create SSL context that doesn't verify certs (common for seedboxes)
        import ssl
        ssl_context = ssl._create_unverified_context()
        https_handler = urllib.request.HTTPSHandler(context=ssl_context)

        # Build opener with both handlers
        opener = urllib.request.build_opener(auth_handler, https_handler)

        # Make request
        req = urllib.request.Request(
            url,
            data=request_body,
            headers={'Content-Type': 'text/xml', 'User-Agent': self.user_agent}
        )

        try:
            response = opener.open(req)
            return self.parse_response(response)
        except urllib.error.HTTPError as e:
            raise Exception(f"HTTP {e.code}: {e.reason}") from e


class RTorrentClient:
    """Client for rtorrent via ruTorrent httprpc plugin.

    Provides high-level methods for common rtorrent operations like
    listing torrents, getting details, and deleting torrents.

    Example:
        >>> client = RTorrentClient('nl3864.dediseedbox.com', 'user', 'pass')
        >>> seeding = client.get_seeding_torrents()
        >>> for hash_id in seeding:
        ...     info = client.get_torrent_info(hash_id)
        ...     print(f"{info['name']}: ratio={info['ratio']:.2f}")
    """

    def __init__(self, host: str, username: str, password: str, use_https: bool = True):
        """Initialize rtorrent client.

        Args:
            host: Seedbox hostname (e.g., 'nl3864.dediseedbox.com')
            username: Seedbox username
            password: Seedbox password
            use_https: Use HTTPS (default True)
        """
        self.host = host
        self.username = username
        self.use_https = use_https
        self.logger = logging.getLogger(self.__class__.__name__)

        # Create transport with Digest auth
        transport = DigestTransport(username, password, use_https)

        # Create XMLRPC server proxy
        protocol = 'https' if use_https else 'http'
        url = f"{protocol}://{host}/rutorrent/plugins/httprpc/action.php"
        self.server = xmlrpc.client.ServerProxy(url, transport=transport)

        # Disable SSL warnings
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _call(self, method: str, *args):
        """Make XMLRPC call with error handling.

        Args:
            method: XMLRPC method name
            *args: Method arguments

        Returns:
            Method result

        Raises:
            Exception: If XMLRPC call fails
        """
        try:
            # Get method from server
            rpc_method = self.server
            for part in method.split('.'):
                rpc_method = getattr(rpc_method, part)

            # Call method
            return rpc_method(*args)

        except xmlrpc.client.Fault as e:
            self.logger.error(f"XMLRPC Fault: {e}")
            raise Exception(f"XMLRPC error: {e}") from e
        except Exception as e:
            self.logger.error(f"Connection error: {e}")
            raise

    def test_connection(self) -> bool:
        """Test connection to rtorrent.

        Returns:
            True if connection successful

        Raises:
            Exception: If connection fails
        """
        methods = self._call('system.listMethods')
        self.logger.info(f"Connected to rtorrent ({len(methods)} methods available)")
        return True

    def get_torrents(self, view: str = 'main') -> List[str]:
        """Get list of torrent hashes in a view.

        IMPORTANT: rtorrent XMLRPC requires empty string as first parameter!

        Args:
            view: View name (main, seeding, started, stopped, leeching)

        Returns:
            List of torrent info hashes (uppercase hex, 40 chars)
        """
        # CRITICAL: Empty string required as first parameter
        return self._call('download_list', '', view)

    def get_seeding_torrents(self) -> List[str]:
        """Get all seeding torrents.

        Returns:
            List of torrent hashes
        """
        return self.get_torrents('seeding')

    def get_torrent_info(self, hash_id: str) -> Dict[str, Any]:
        """Get detailed information about a torrent.

        Args:
            hash_id: Torrent info hash

        Returns:
            Dict with torrent details:
                - hash: Torrent hash
                - name: Torrent name
                - size_bytes: Total size in bytes
                - completed_bytes: Downloaded bytes
                - ratio: Actual ratio (already divided by 1000)
                - is_active: True if transferring
                - is_complete: True if download complete
                - directory: Download directory path
                - timestamp_finished: Unix timestamp when finished
                - timestamp_started: Unix timestamp when started
                - label: Custom label (from custom1 field)
        """
        try:
            # Get all details in one go
            name = self._call('d.name', hash_id)
            size_bytes = self._call('d.size_bytes', hash_id)
            completed_bytes = self._call('d.completed_bytes', hash_id)
            ratio_raw = self._call('d.ratio', hash_id)
            is_active = self._call('d.is_active', hash_id)
            is_complete = self._call('d.complete', hash_id)
            directory = self._call('d.directory', hash_id)
            timestamp_finished = self._call('d.timestamp.finished', hash_id)
            timestamp_started = self._call('d.timestamp.started', hash_id)

            # Try to get label (may not exist)
            try:
                label = self._call('d.custom1', hash_id)
            except:
                label = ''

            return {
                'hash': hash_id,
                'name': name,
                'size_bytes': size_bytes,
                'completed_bytes': completed_bytes,
                'ratio': ratio_raw / 1000.0,  # CRITICAL: Divide by 1000!
                'ratio_raw': ratio_raw,  # Keep raw value for policy checks
                'is_active': bool(is_active),
                'is_complete': bool(is_complete),
                'directory': directory,
                'timestamp_finished': timestamp_finished,
                'timestamp_started': timestamp_started,
                'label': label
            }

        except Exception as e:
            self.logger.error(f"Error getting info for {hash_id}: {e}")
            raise

    def get_all_torrents_info(self, view: str = 'main') -> List[Dict[str, Any]]:
        """Get information about all torrents in a view.

        Args:
            view: View name

        Returns:
            List of torrent info dicts
        """
        hashes = self.get_torrents(view)
        torrents = []

        for hash_id in hashes:
            try:
                info = self.get_torrent_info(hash_id)
                torrents.append(info)
            except Exception as e:
                self.logger.warning(f"Could not get info for {hash_id}: {e}")

        return torrents

    def delete_torrent(self, hash_id: str, delete_files: bool = True) -> bool:
        """Delete a torrent.

        Args:
            hash_id: Torrent info hash
            delete_files: If True, delete files; if False, keep files

        Returns:
            True if successful

        Raises:
            Exception: If deletion fails
        """
        try:
            if delete_files:
                # Remove torrent AND delete files
                self._call('d.delete_tied', hash_id)
                self.logger.info(f"Deleted torrent with files: {hash_id}")
            else:
                # Remove torrent but keep files
                self._call('d.erase', hash_id)
                self.logger.info(f"Removed torrent (kept files): {hash_id}")

            return True

        except Exception as e:
            self.logger.error(f"Error deleting {hash_id}: {e}")
            raise

    def get_global_stats(self) -> Dict[str, int]:
        """Get global rtorrent statistics.

        Returns:
            Dict with bandwidth stats:
                - down_rate: Current download rate (bytes/sec)
                - up_rate: Current upload rate (bytes/sec)
                - down_total: Total downloaded (bytes)
                - up_total: Total uploaded (bytes)
        """
        try:
            return {
                'down_rate': self._call('throttle.global_down.rate'),
                'up_rate': self._call('throttle.global_up.rate'),
                'down_total': self._call('throttle.global_down.total'),
                'up_total': self._call('throttle.global_up.total')
            }
        except Exception as e:
            self.logger.error(f"Error getting global stats: {e}")
            return {}

    def start_torrent(self, hash_id: str):
        """Start a torrent.

        Args:
            hash_id: Torrent info hash
        """
        self._call('d.start', hash_id)
        self.logger.info(f"Started torrent: {hash_id}")

    def stop_torrent(self, hash_id: str):
        """Stop a torrent.

        Args:
            hash_id: Torrent info hash
        """
        self._call('d.stop', hash_id)
        self.logger.info(f"Stopped torrent: {hash_id}")


def load_secrets(secrets_file: str) -> Dict[str, str]:
    """Load configuration from secrets file.

    Args:
        secrets_file: Path to secrets.conf file

    Returns:
        Dict of configuration values
    """
    secrets = {}
    with open(secrets_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                secrets[key.strip()] = value.strip().strip('"\'')
    return secrets
