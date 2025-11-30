"""API clients for Radarr, Sonarr, Jellyfin, and Prowlarr.

This module provides wrapper classes for interacting with *arr and Jellyfin APIs.
All clients handle authentication, error handling, and retries automatically.

Example:
    >>> from utils.api_clients import RadarrAPI
    >>> radarr = RadarrAPI('http://localhost:7878', 'api_key_here')
    >>> movies = radarr.get_movies()
    >>> print(f"Found {len(movies)} movies")
"""

import requests
from typing import List, Dict, Any, Optional
import logging
import time


class APIError(Exception):
    """Raised when API request fails."""
    pass


class BaseAPI:
    """Base class for *arr API clients.

    Provides common functionality for all API clients including
    authentication, error handling, and retries.
    """

    def __init__(self, url: str, api_key: str, timeout: int = 30):
        """Initialize API client.

        Args:
            url: Base URL of the service (e.g., 'http://localhost:7878')
            api_key: API key for authentication
            timeout: Request timeout in seconds
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.logger = logging.getLogger(self.__class__.__name__)

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with API key."""
        return {'X-Api-Key': self.api_key}

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
        retries: int = 3
    ) -> Any:
        """Make API request with retries.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path
            params: Query parameters
            json: JSON body for POST/PUT requests
            retries: Number of retry attempts

        Returns:
            Response JSON data

        Raises:
            APIError: If request fails after all retries
        """
        url = f"{self.url}{endpoint}"

        for attempt in range(retries):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    params=params,
                    json=json,
                    timeout=self.timeout
                )

                response.raise_for_status()

                # Some endpoints return empty response
                if not response.content:
                    return {}

                return response.json()

            except requests.exceptions.Timeout:
                self.logger.warning(f"Request timeout (attempt {attempt + 1}/{retries})")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise APIError(f"Request timed out after {retries} attempts")

            except requests.exceptions.HTTPError as e:
                if e.response.status_code in [401, 403]:
                    raise APIError(f"Authentication failed: {e}")
                elif e.response.status_code == 404:
                    raise APIError(f"Endpoint not found: {endpoint}")
                else:
                    self.logger.warning(f"HTTP error (attempt {attempt + 1}/{retries}): {e}")
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                    else:
                        raise APIError(f"HTTP error: {e}")

            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Request failed (attempt {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise APIError(f"Request failed: {e}")

        raise APIError("Unexpected error in request retry loop")


class RadarrAPI(BaseAPI):
    """Radarr API v3 client.

    Example:
        >>> radarr = RadarrAPI('http://localhost:7878', 'api_key')
        >>> movies = radarr.get_movies()
        >>> radarr.add_tag(movie_id=1, tag='delete-candidate')
    """

    def get_movies(self) -> List[Dict[str, Any]]:
        """Get all movies.

        Returns:
            List of movie dictionaries
        """
        return self._request('GET', '/api/v3/movie')

    def get_movie(self, movie_id: int) -> Dict[str, Any]:
        """Get movie by ID.

        Args:
            movie_id: Movie ID

        Returns:
            Movie dictionary
        """
        return self._request('GET', f'/api/v3/movie/{movie_id}')

    def delete_movie(self, movie_id: int, delete_files: bool = True) -> Dict:
        """Delete a movie.

        Args:
            movie_id: Movie ID
            delete_files: Whether to delete files from disk

        Returns:
            Response dictionary
        """
        params = {'deleteFiles': 'true' if delete_files else 'false'}
        return self._request('DELETE', f'/api/v3/movie/{movie_id}', params=params)

    def get_tags(self) -> List[Dict[str, Any]]:
        """Get all tags.

        Returns:
            List of tag dictionaries
        """
        return self._request('GET', '/api/v3/tag')

    def create_tag(self, label: str) -> Dict[str, Any]:
        """Create a new tag.

        Args:
            label: Tag label

        Returns:
            Created tag dictionary
        """
        return self._request('POST', '/api/v3/tag', json={'label': label})

    def add_tag(self, movie_id: int, tag_label: str) -> Dict[str, Any]:
        """Add tag to a movie.

        Args:
            movie_id: Movie ID
            tag_label: Tag label to add

        Returns:
            Updated movie dictionary
        """
        # Get existing tags
        tags = self.get_tags()
        tag_dict = {t['label']: t['id'] for t in tags}

        # Create tag if it doesn't exist
        if tag_label not in tag_dict:
            new_tag = self.create_tag(tag_label)
            tag_id = new_tag['id']
        else:
            tag_id = tag_dict[tag_label]

        # Get movie and update tags
        movie = self.get_movie(movie_id)
        movie_tags = movie.get('tags', [])

        if tag_id not in movie_tags:
            movie_tags.append(tag_id)
            movie['tags'] = movie_tags
            return self._request('PUT', f'/api/v3/movie/{movie_id}', json=movie)

        return movie

    def get_history(self, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get history events.

        Args:
            event_type: Filter by event type (e.g., 'grabbed', 'downloadFolderImported')

        Returns:
            List of history events
        """
        params = {'eventType': event_type} if event_type else {}
        response = self._request('GET', '/api/v3/history', params=params)
        return response.get('records', [])

    def get_queue(self) -> List[Dict[str, Any]]:
        """Get download queue.

        Returns:
            List of queued downloads
        """
        response = self._request('GET', '/api/v3/queue')
        return response.get('records', [])


class SonarrAPI(BaseAPI):
    """Sonarr API v3 client.

    Example:
        >>> sonarr = SonarrAPI('http://localhost:8989', 'api_key')
        >>> series = sonarr.get_series()
        >>> sonarr.add_tag(series_id=1, tag='delete-candidate')
    """

    def get_series(self) -> List[Dict[str, Any]]:
        """Get all series.

        Returns:
            List of series dictionaries
        """
        return self._request('GET', '/api/v3/series')

    def get_series_by_id(self, series_id: int) -> Dict[str, Any]:
        """Get series by ID.

        Args:
            series_id: Series ID

        Returns:
            Series dictionary
        """
        return self._request('GET', f'/api/v3/series/{series_id}')

    def get_episodes(self, series_id: int) -> List[Dict[str, Any]]:
        """Get episodes for a series.

        Args:
            series_id: Series ID

        Returns:
            List of episode dictionaries
        """
        return self._request('GET', '/api/v3/episode', params={'seriesId': series_id})

    def delete_series(self, series_id: int, delete_files: bool = True) -> Dict:
        """Delete a series.

        Args:
            series_id: Series ID
            delete_files: Whether to delete files from disk

        Returns:
            Response dictionary
        """
        params = {'deleteFiles': 'true' if delete_files else 'false'}
        return self._request('DELETE', f'/api/v3/series/{series_id}', params=params)

    def get_tags(self) -> List[Dict[str, Any]]:
        """Get all tags.

        Returns:
            List of tag dictionaries
        """
        return self._request('GET', '/api/v3/tag')

    def create_tag(self, label: str) -> Dict[str, Any]:
        """Create a new tag.

        Args:
            label: Tag label

        Returns:
            Created tag dictionary
        """
        return self._request('POST', '/api/v3/tag', json={'label': label})

    def add_tag(self, series_id: int, tag_label: str) -> Dict[str, Any]:
        """Add tag to a series.

        Args:
            series_id: Series ID
            tag_label: Tag label to add

        Returns:
            Updated series dictionary
        """
        # Get existing tags
        tags = self.get_tags()
        tag_dict = {t['label']: t['id'] for t in tags}

        # Create tag if it doesn't exist
        if tag_label not in tag_dict:
            new_tag = self.create_tag(tag_label)
            tag_id = new_tag['id']
        else:
            tag_id = tag_dict[tag_label]

        # Get series and update tags
        series = self.get_series_by_id(series_id)
        series_tags = series.get('tags', [])

        if tag_id not in series_tags:
            series_tags.append(tag_id)
            series['tags'] = series_tags
            return self._request('PUT', f'/api/v3/series/{series_id}', json=series)

        return series

    def get_history(self, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get history events.

        Args:
            event_type: Filter by event type (e.g., 'grabbed', 'downloadFolderImported')

        Returns:
            List of history events
        """
        params = {'eventType': event_type} if event_type else {}
        response = self._request('GET', '/api/v3/history', params=params)
        return response.get('records', [])


class JellyfinAPI:
    """Jellyfin API client.

    Note: Jellyfin uses X-Emby-Token instead of X-Api-Key.

    Example:
        >>> jellyfin = JellyfinAPI('http://jellyfin.home:8096', 'api_key')
        >>> jellyfin.refresh_library()
    """

    def __init__(self, url: str, api_key: str, timeout: int = 30):
        """Initialize Jellyfin API client.

        Args:
            url: Base URL of Jellyfin server
            api_key: API key for authentication
            timeout: Request timeout in seconds
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.logger = logging.getLogger(self.__class__.__name__)

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with API token."""
        return {'X-Emby-Token': self.api_key}

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None
    ) -> Any:
        """Make API request.

        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            json: JSON body

        Returns:
            Response JSON or empty dict

        Raises:
            APIError: If request fails
        """
        url = f"{self.url}{endpoint}"

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=self._get_headers(),
                params=params,
                json=json,
                timeout=self.timeout
            )

            response.raise_for_status()

            if not response.content:
                return {}

            return response.json()

        except requests.exceptions.RequestException as e:
            raise APIError(f"Jellyfin API request failed: {e}")

    def refresh_library(self, item_id: Optional[str] = None) -> Dict:
        """Trigger library refresh.

        Args:
            item_id: Optional specific item ID to refresh (full scan if None)

        Returns:
            Response dictionary
        """
        params = {'itemId': item_id} if item_id else {}
        return self._request('POST', '/Library/Refresh', params=params)

    def get_items(
        self,
        user_id: Optional[str] = None,
        include_item_types: Optional[str] = None,
        filters: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get library items.

        Args:
            user_id: User ID (optional for basic queries)
            include_item_types: Item types (e.g., 'Movie,Series')
            filters: Filters (e.g., 'IsUnplayed')

        Returns:
            List of items
        """
        # Use a default system query if no user_id provided
        endpoint = f'/Users/{user_id}/Items' if user_id else '/Items'

        params = {
            'Recursive': 'true',
            'Fields': 'Path,MediaStreams,ProviderIds,CommunityRating'
        }

        if include_item_types:
            params['IncludeItemTypes'] = include_item_types
        if filters:
            params['Filters'] = filters

        response = self._request('GET', endpoint, params=params)
        return response.get('Items', [])


class ProwlarrAPI(BaseAPI):
    """Prowlarr API v1 client.

    Example:
        >>> prowlarr = ProwlarrAPI('http://localhost:9696', 'api_key')
        >>> indexers = prowlarr.get_indexers()
    """

    def get_indexers(self) -> List[Dict[str, Any]]:
        """Get all indexers.

        Returns:
            List of indexer dictionaries
        """
        return self._request('GET', '/api/v1/indexer')

    def search(self, query: str, indexer_ids: Optional[List[int]] = None) -> List[Dict]:
        """Search across indexers.

        Args:
            query: Search query
            indexer_ids: Optional list of specific indexer IDs

        Returns:
            List of search results
        """
        params = {'query': query}
        if indexer_ids:
            params['indexerIds'] = ','.join(map(str, indexer_ids))

        return self._request('GET', '/api/v1/search', params=params)


# Example usage
if __name__ == '__main__':
    import sys

    print("Testing API clients...")
    print("(Configure API keys in config.yaml first)\n")

    from utils.config_loader import load_config

    try:
        config = load_config('config.yaml')

        # Test Radarr
        print("Testing Radarr API...")
        radarr = RadarrAPI(config['radarr']['url'], config['radarr']['api_key'])
        movies = radarr.get_movies()
        print(f"  Found {len(movies)} movies")

        # Test Sonarr
        print("\nTesting Sonarr API...")
        sonarr = SonarrAPI(config['sonarr']['url'], config['sonarr']['api_key'])
        series = sonarr.get_series()
        print(f"  Found {len(series)} series")

        # Test Jellyfin
        print("\nTesting Jellyfin API...")
        jellyfin = JellyfinAPI(config['jellyfin']['url'], config['jellyfin']['api_key'])
        print("  Triggering library refresh...")
        jellyfin.refresh_library()
        print("  Library refresh triggered successfully")

        print("\nAll API clients working!")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
