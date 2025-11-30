"""TMDB API client for metadata enrichment and age rating detection.

This module provides access to The Movie Database (TMDB) API for:
- Fetching movie/TV show metadata
- Retrieving content ratings and certifications
- Searching for content by title and year
- Updating missing ratings in Radarr/Sonarr

API Documentation: https://developers.themoviedb.org/3

Example:
    >>> from utils.tmdb_client import TMDBClient
    >>> tmdb = TMDBClient(api_key='your_key')
    >>> rating = tmdb.get_movie_certification('The Lion King', 1994)
    >>> print(rating)  # 'G'
"""

import requests
from typing import Optional, Dict, Any, List
import logging


class TMDBClient:
    """TMDB API client for movie and TV show metadata.

    Attributes:
        api_key: TMDB API key
        base_url: TMDB API base URL
        language: Language for metadata (default: en-US)
        include_adult: Whether to include adult content in searches
    """

    def __init__(self, api_key: str, language: str = "en-US", include_adult: bool = False):
        """Initialize TMDB client.

        Args:
            api_key: TMDB API key (get from https://www.themoviedb.org/settings/api)
            language: Language code for metadata
            include_adult: Include adult content in searches

        Raises:
            ValueError: If api_key is empty
        """
        if not api_key:
            raise ValueError("TMDB API key is required")

        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
        self.language = language
        self.include_adult = include_adult
        self.logger = logging.getLogger(__name__)

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Make API request to TMDB.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., '/search/movie')
            params: Query parameters

        Returns:
            JSON response as dictionary

        Raises:
            requests.exceptions.RequestException: On API errors
        """
        url = f"{self.base_url}{endpoint}"

        # Add API key and language to all requests
        params = params or {}
        params['api_key'] = self.api_key
        params['language'] = self.language

        try:
            response = requests.request(method, url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            self.logger.error(f"TMDB request timed out: {endpoint}")
            raise
        except requests.exceptions.HTTPError as e:
            self.logger.error(f"TMDB HTTP error: {e}")
            raise
        except requests.exceptions.RequestException as e:
            self.logger.error(f"TMDB request failed: {e}")
            raise

    def search_movie(self, title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search for a movie by title.

        Args:
            title: Movie title
            year: Release year (optional, helps narrow results)

        Returns:
            List of matching movies with metadata

        Example:
            >>> results = tmdb.search_movie('The Lion King', 1994)
            >>> results[0]['id']  # 8587
        """
        params = {
            'query': title,
            'include_adult': self.include_adult
        }

        if year:
            params['year'] = year

        response = self._request('GET', '/search/movie', params)
        return response.get('results', [])

    def search_tv(self, title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search for a TV show by title.

        Args:
            title: TV show title
            year: First air year (optional)

        Returns:
            List of matching TV shows with metadata

        Example:
            >>> results = tmdb.search_tv('Breaking Bad', 2008)
            >>> results[0]['id']  # 1396
        """
        params = {
            'query': title,
            'include_adult': self.include_adult
        }

        if year:
            params['first_air_date_year'] = year

        response = self._request('GET', '/search/tv', params)
        return response.get('results', [])

    def get_movie_details(self, tmdb_id: int) -> Dict[str, Any]:
        """Get detailed information about a movie.

        Args:
            tmdb_id: TMDB movie ID

        Returns:
            Movie details including title, release date, overview, etc.

        Example:
            >>> details = tmdb.get_movie_details(8587)
            >>> details['title']  # 'The Lion King'
        """
        return self._request('GET', f'/movie/{tmdb_id}')

    def get_tv_details(self, tmdb_id: int) -> Dict[str, Any]:
        """Get detailed information about a TV show.

        Args:
            tmdb_id: TMDB TV show ID

        Returns:
            TV show details including name, first air date, overview, etc.

        Example:
            >>> details = tmdb.get_tv_details(1396)
            >>> details['name']  # 'Breaking Bad'
        """
        return self._request('GET', f'/tv/{tmdb_id}')

    def get_movie_certification(self, title: str, year: Optional[int] = None) -> Optional[str]:
        """Get US certification for a movie (G, PG, PG-13, R, etc.).

        Args:
            title: Movie title
            year: Release year (optional, helps accuracy)

        Returns:
            US certification string or None if not found

        Example:
            >>> cert = tmdb.get_movie_certification('The Lion King', 1994)
            >>> cert  # 'G'
        """
        # Search for the movie
        results = self.search_movie(title, year)

        if not results:
            self.logger.warning(f"Movie not found: {title} ({year})")
            return None

        # Get the first result (usually most relevant)
        movie_id = results[0]['id']

        # Get release dates (includes certifications)
        try:
            response = self._request('GET', f'/movie/{movie_id}/release_dates')

            # Find US certification
            for country_data in response.get('results', []):
                if country_data['iso_3166_1'] == 'US':
                    release_dates = country_data.get('release_dates', [])
                    if release_dates:
                        certification = release_dates[0].get('certification', '')
                        if certification:
                            self.logger.debug(f"Found certification for {title}: {certification}")
                            return certification

            self.logger.warning(f"No US certification found for: {title}")
            return None

        except Exception as e:
            self.logger.error(f"Error fetching certification for {title}: {e}")
            return None

    def get_tv_certification(self, title: str, year: Optional[int] = None) -> Optional[str]:
        """Get US content rating for a TV show (TV-Y, TV-PG, TV-14, etc.).

        Args:
            title: TV show title
            year: First air year (optional)

        Returns:
            US content rating string or None if not found

        Example:
            >>> rating = tmdb.get_tv_certification('Avatar: The Last Airbender', 2005)
            >>> rating  # 'TV-Y7'
        """
        # Search for the TV show
        results = self.search_tv(title, year)

        if not results:
            self.logger.warning(f"TV show not found: {title} ({year})")
            return None

        # Get the first result
        tv_id = results[0]['id']

        # Get content ratings
        try:
            response = self._request('GET', f'/tv/{tv_id}/content_ratings')

            # Find US rating
            for rating_data in response.get('results', []):
                if rating_data['iso_3166_1'] == 'US':
                    rating = rating_data.get('rating', '')
                    if rating:
                        self.logger.debug(f"Found rating for {title}: {rating}")
                        return rating

            self.logger.warning(f"No US rating found for: {title}")
            return None

        except Exception as e:
            self.logger.error(f"Error fetching rating for {title}: {e}")
            return None

    def is_kids_content(self, title: str, year: Optional[int], content_type: str,
                       kids_ratings: List[str]) -> bool:
        """Determine if content is appropriate for kids based on age rating.

        Args:
            title: Content title
            year: Release/air year
            content_type: 'movie' or 'series'
            kids_ratings: List of certifications considered safe for kids

        Returns:
            True if content has a kids-safe rating, False otherwise

        Example:
            >>> is_kids = tmdb.is_kids_content('The Lion King', 1994, 'movie', ['G', 'PG'])
            >>> is_kids  # True
        """
        if content_type == 'movie':
            cert = self.get_movie_certification(title, year)
        elif content_type == 'series':
            cert = self.get_tv_certification(title, year)
        else:
            self.logger.error(f"Invalid content_type: {content_type}")
            return False

        if cert:
            is_kids = cert in kids_ratings
            self.logger.info(f"{title} ({year}): {cert} - Kids content: {is_kids}")
            return is_kids
        else:
            # If no rating found, default to NOT kids content (safer)
            self.logger.warning(f"No rating found for {title} ({year}), defaulting to adult library")
            return False


def create_tmdb_client(config: Dict[str, Any]) -> Optional[TMDBClient]:
    """Factory function to create TMDB client from config.

    Args:
        config: Full configuration dictionary

    Returns:
        Configured TMDBClient instance or None if API key missing

    Example:
        >>> from utils.config_loader import load_config
        >>> config = load_config('config.yaml')
        >>> tmdb = create_tmdb_client(config)
    """
    tmdb_config = config.get('tmdb', {})
    api_key = tmdb_config.get('api_key', '')

    if not api_key:
        logging.warning("TMDB API key not configured, metadata enrichment disabled")
        return None

    return TMDBClient(
        api_key=api_key,
        language=tmdb_config.get('language', 'en-US'),
        include_adult=tmdb_config.get('include_adult', False)
    )


# Example usage
if __name__ == '__main__':
    import sys

    # Test configuration
    test_api_key = input("Enter your TMDB API key: ").strip()

    if not test_api_key:
        print("API key required for testing")
        sys.exit(1)

    tmdb = TMDBClient(test_api_key)

    print("\nTesting TMDB API client...\n")

    # Test movie search and certification
    print("1. Testing movie certification:")
    cert = tmdb.get_movie_certification('The Lion King', 1994)
    print(f"   The Lion King (1994): {cert}")

    cert = tmdb.get_movie_certification('The Matrix', 1999)
    print(f"   The Matrix (1999): {cert}")

    # Test TV show search and rating
    print("\n2. Testing TV show rating:")
    rating = tmdb.get_tv_certification('Avatar: The Last Airbender', 2005)
    print(f"   Avatar: The Last Airbender (2005): {rating}")

    rating = tmdb.get_tv_certification('Breaking Bad', 2008)
    print(f"   Breaking Bad (2008): {rating}")

    # Test kids content detection
    print("\n3. Testing kids content detection:")
    kids_movie_ratings = ['G', 'PG', 'TV-Y', 'TV-Y7', 'TV-G']
    kids_series_ratings = ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG']

    is_kids = tmdb.is_kids_content('The Lion King', 1994, 'movie', kids_movie_ratings)
    print(f"   The Lion King - Kids content: {is_kids}")

    is_kids = tmdb.is_kids_content('The Matrix', 1999, 'movie', kids_movie_ratings)
    print(f"   The Matrix - Kids content: {is_kids}")

    print("\nAll tests complete!")
