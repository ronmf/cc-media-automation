"""Configuration loader with API key auto-extraction from XML files.

This module handles loading the YAML configuration and automatically extracts
API keys from Radarr/Sonarr/Prowlarr config.xml files if not provided in the YAML.

Example:
    >>> from utils.config_loader import load_config
    >>> config = load_config('config.yaml')
    >>> print(config['radarr']['api_key'])
    'abc123...'
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
import xml.etree.ElementTree as ET


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""
    pass


def extract_api_key_from_xml(xml_path: str) -> Optional[str]:
    """Extract API key from *arr application config.xml file.

    Args:
        xml_path: Path to the config.xml file

    Returns:
        API key string if found, None otherwise

    Example:
        >>> key = extract_api_key_from_xml('/opt/arr-stack/radarr/config.xml')
        >>> print(key)
        'abc123def456...'
    """
    if not os.path.exists(xml_path):
        return None

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Look for ApiKey element
        api_key_elem = root.find('.//ApiKey')
        if api_key_elem is not None and api_key_elem.text:
            return api_key_elem.text.strip()

        return None
    except ET.ParseError as e:
        print(f"Warning: Failed to parse XML file {xml_path}: {e}")
        return None
    except Exception as e:
        print(f"Warning: Error reading {xml_path}: {e}")
        return None


def auto_populate_api_keys(config: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-populate missing API keys from *arr config.xml files.

    For each *arr service (radarr, sonarr, prowlarr), if the api_key is empty
    and config_path is specified, attempt to extract the API key from the XML.

    Args:
        config: Configuration dictionary

    Returns:
        Updated configuration dictionary with API keys populated
    """
    services = ['radarr', 'sonarr', 'prowlarr']

    for service in services:
        if service not in config:
            continue

        service_config = config[service]

        # Check if API key is missing or empty
        if not service_config.get('api_key'):
            config_path = service_config.get('config_path')
            if config_path:
                api_key = extract_api_key_from_xml(config_path)
                if api_key:
                    service_config['api_key'] = api_key
                    print(f"Auto-populated {service.capitalize()} API key from {config_path}")
                else:
                    print(f"Warning: Could not extract API key from {config_path}")

    return config


def validate_config(config: Dict[str, Any]) -> None:
    """Validate that required configuration sections exist.

    Args:
        config: Configuration dictionary

    Raises:
        ConfigError: If required configuration is missing
    """
    required_sections = ['seedbox', 'paths', 'radarr', 'sonarr', 'jellyfin', 'notifications', 'thresholds']

    for section in required_sections:
        if section not in config:
            raise ConfigError(f"Missing required configuration section: {section}")

    # Validate seedbox config
    if not config['seedbox'].get('host'):
        raise ConfigError("Seedbox host is required")
    if not config['seedbox'].get('username'):
        raise ConfigError("Seedbox username is required")
    if not config['seedbox'].get('password'):
        raise ConfigError("Seedbox password is required (no SSH key auth)")

    # Validate API keys
    if not config['radarr'].get('api_key'):
        raise ConfigError("Radarr API key is required (could not auto-extract)")
    if not config['sonarr'].get('api_key'):
        raise ConfigError("Sonarr API key is required (could not auto-extract)")
    if not config['jellyfin'].get('api_key'):
        raise ConfigError("Jellyfin API key is required")

    # Validate paths exist
    required_paths = ['media_root', 'downloads_done', 'scripts', 'logs']
    for path_key in required_paths:
        path = config['paths'].get(path_key)
        if not path:
            raise ConfigError(f"Path '{path_key}' is required")


def load_config(config_path: str = 'config.yaml', validate: bool = True) -> Dict[str, Any]:
    """Load configuration from YAML file with API key auto-extraction.

    This function loads the YAML configuration and automatically populates
    missing API keys by extracting them from *arr config.xml files.

    Args:
        config_path: Path to the YAML configuration file
        validate: Whether to validate the configuration (default: True)

    Returns:
        Configuration dictionary

    Raises:
        ConfigError: If configuration is invalid or missing

    Example:
        >>> config = load_config('config.yaml')
        >>> print(config['seedbox']['host'])
        '185.56.20.18'
    """
    if not os.path.exists(config_path):
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML configuration: {e}")
    except Exception as e:
        raise ConfigError(f"Failed to load configuration: {e}")

    if not config:
        raise ConfigError("Configuration file is empty")

    # Auto-populate API keys from XML files
    config = auto_populate_api_keys(config)

    # Validate configuration if requested
    if validate:
        validate_config(config)

    return config


def get_config_value(config: Dict[str, Any], key_path: str, default: Any = None) -> Any:
    """Get a configuration value using dot notation.

    Args:
        config: Configuration dictionary
        key_path: Dot-separated path to the value (e.g., 'seedbox.host')
        default: Default value if key not found

    Returns:
        Configuration value or default

    Example:
        >>> config = load_config('config.yaml')
        >>> host = get_config_value(config, 'seedbox.host')
        >>> print(host)
        '185.56.20.18'
    """
    keys = key_path.split('.')
    value = config

    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
            if value is None:
                return default
        else:
            return default

    return value


def mask_secrets(config: Dict[str, Any]) -> Dict[str, Any]:
    """Create a copy of config with secrets masked for display.

    Args:
        config: Configuration dictionary

    Returns:
        Configuration dictionary with secrets replaced by '****'

    Example:
        >>> config = load_config('config.yaml')
        >>> safe_config = mask_secrets(config)
        >>> print(safe_config['seedbox']['password'])
        '****'
    """
    import copy
    masked = copy.deepcopy(config)

    # Mask sensitive fields
    secret_keys = ['password', 'api_key']

    def mask_dict(d):
        for key, value in d.items():
            if key in secret_keys and value:
                d[key] = '****'
            elif isinstance(value, dict):
                mask_dict(value)

    mask_dict(masked)
    return masked


# Example usage
if __name__ == '__main__':
    import sys

    config_file = sys.argv[1] if len(sys.argv) > 1 else 'config.yaml'

    try:
        print(f"Loading configuration from {config_file}...")
        config = load_config(config_file)

        print("\n=== Configuration Loaded Successfully ===\n")

        print("Seedbox:", config['seedbox']['host'])
        print("Radarr:", config['radarr']['url'])
        print("Sonarr:", config['sonarr']['url'])
        print("Jellyfin:", config['jellyfin']['url'])
        print("ntfy:", config['notifications']['ntfy']['url'])

        print("\nAPI Keys:")
        print("  Radarr:", "✓" if config['radarr']['api_key'] else "✗ MISSING")
        print("  Sonarr:", "✓" if config['sonarr']['api_key'] else "✗ MISSING")
        print("  Jellyfin:", "✓" if config['jellyfin']['api_key'] else "✗ MISSING")

        print("\nConfiguration is valid!")

    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
