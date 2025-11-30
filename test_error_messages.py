#!/usr/bin/env python3
"""Test script to demonstrate improved error message parsing.

This script shows the difference between raw JSON error responses
and the new human-readable error messages.
"""

from utils.api_clients import parse_validation_errors


def test_validation_error_parser():
    """Test the validation error parser with various error types."""

    print("Testing Validation Error Parser")
    print("=" * 60)

    # Test 1: Movie already exists
    print("\nTest 1: Movie Already Exists")
    print("-" * 60)
    raw_error = [
        {
            'propertyName': 'TmdbId',
            'errorMessage': 'This movie has already been added',
            'attemptedValue': 550,
            'severity': 'error',
            'errorCode': 'MovieExistsValidator',
            'formattedMessageArguments': [],
            'formattedMessagePlaceholderValues': {}
        }
    ]
    print(f"Raw JSON: {raw_error}")
    print(f"Parsed: {parse_validation_errors(raw_error)}")

    # Test 2: Series already exists
    print("\nTest 2: Series Already Exists")
    print("-" * 60)
    raw_error = [
        {
            'propertyName': 'TvdbId',
            'errorMessage': 'This series has already been added',
            'attemptedValue': 305074,
            'severity': 'error',
            'errorCode': 'SeriesExistsValidator',
            'formattedMessageArguments': [],
            'formattedMessagePlaceholderValues': {}
        }
    ]
    print(f"Raw JSON: {raw_error}")
    print(f"Parsed: {parse_validation_errors(raw_error)}")

    # Test 3: Invalid root folder
    print("\nTest 3: Invalid Root Folder")
    print("-" * 60)
    raw_error = [
        {
            'propertyName': 'RootFolderPath',
            'errorMessage': 'Root folder path is invalid',
            'attemptedValue': '/invalid/path',
            'severity': 'error',
            'errorCode': 'RootFolderValidator',
            'formattedMessageArguments': [],
            'formattedMessagePlaceholderValues': {}
        }
    ]
    print(f"Raw JSON: {raw_error}")
    print(f"Parsed: {parse_validation_errors(raw_error)}")

    # Test 4: Invalid quality profile
    print("\nTest 4: Invalid Quality Profile")
    print("-" * 60)
    raw_error = [
        {
            'propertyName': 'QualityProfileId',
            'errorMessage': 'Quality profile does not exist',
            'attemptedValue': 999,
            'severity': 'error',
            'errorCode': 'QualityProfileValidator',
            'formattedMessageArguments': [],
            'formattedMessagePlaceholderValues': {}
        }
    ]
    print(f"Raw JSON: {raw_error}")
    print(f"Parsed: {parse_validation_errors(raw_error)}")

    # Test 5: Multiple errors
    print("\nTest 5: Multiple Validation Errors")
    print("-" * 60)
    raw_error = [
        {
            'propertyName': 'RootFolderPath',
            'errorMessage': 'Root folder path is invalid',
            'attemptedValue': '/invalid/path',
            'severity': 'error',
            'errorCode': 'RootFolderValidator'
        },
        {
            'propertyName': 'QualityProfileId',
            'errorMessage': 'Quality profile does not exist',
            'attemptedValue': 999,
            'severity': 'error',
            'errorCode': 'QualityProfileValidator'
        }
    ]
    print(f"Raw JSON: {raw_error}")
    print(f"Parsed: {parse_validation_errors(raw_error)}")

    # Test 6: Invalid path
    print("\nTest 6: Invalid Path")
    print("-" * 60)
    raw_error = [
        {
            'propertyName': 'Path',
            'errorMessage': 'Path contains invalid characters',
            'attemptedValue': '/mnt/media/movies/<invalid>',
            'severity': 'error',
            'errorCode': 'PathValidator'
        }
    ]
    print(f"Raw JSON: {raw_error}")
    print(f"Parsed: {parse_validation_errors(raw_error)}")

    # Test 7: Generic error message
    print("\nTest 7: Generic Error Message")
    print("-" * 60)
    raw_error = [
        {
            'propertyName': 'Title',
            'errorMessage': 'Title is required',
            'attemptedValue': None,
            'severity': 'error',
            'errorCode': 'NotEmptyValidator'
        }
    ]
    print(f"Raw JSON: {raw_error}")
    print(f"Parsed: {parse_validation_errors(raw_error)}")

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("\nExpected Output Format:")
    print("✅ 'Movie already exists (TMDB ID: 550)'")
    print("✅ 'Series already exists (TVDB ID: 305074)'")
    print("✅ 'Invalid root folder path: /invalid/path'")
    print("✅ 'Invalid quality profile: 999'")
    print("✅ 'Invalid path: /mnt/media/movies/<invalid>'")
    print("✅ 'Title is required'")


if __name__ == '__main__':
    test_validation_error_parser()
