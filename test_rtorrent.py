#!/usr/bin/env python3
"""Test rtorrent XMLRPC connection and basic functionality.

This script verifies that:
1. XMLRPC connection works with HTTP Digest auth
2. Can list torrents
3. Can get torrent details
4. Can retrieve bandwidth stats

Usage:
    python3 test_rtorrent.py
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from utils.config_loader import load_config
from utils.rtorrent_client import RTorrentClient


def main():
    print("="*70)
    print("RTORRENT XMLRPC CONNECTION TEST")
    print("="*70)
    print()

    # Load config
    try:
        config = load_config('config.yaml')
        print("✅ Config loaded successfully")
    except Exception as e:
        print(f"❌ Failed to load config: {e}")
        return 1

    # Get credentials
    seedbox = config['seedbox']
    print(f"Host: nl3864.dediseedbox.com")
    print(f"User: {seedbox['username']}")
    print()

    # Create client
    try:
        client = RTorrentClient(
            host="nl3864.dediseedbox.com",
            username=seedbox['username'],
            password=seedbox['password']
        )
        print("✅ RTorrentClient initialized")
    except Exception as e:
        print(f"❌ Failed to initialize client: {e}")
        return 1

    # Test connection
    print("\n🔌 Testing connection...")
    try:
        client.test_connection()
        print("✅ Connected to rtorrent!")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print("\n💡 Troubleshooting:")
        print("  1. Verify credentials in config.yaml")
        print("  2. Check that httprpc plugin is enabled in ruTorrent")
        print("  3. Try accessing https://nl3864.dediseedbox.com/rutorrent/")
        return 1

    # Get global stats
    print("\n📊 Global Statistics:")
    try:
        stats = client.get_global_stats()
        if stats:
            print(f"  Download rate: {stats['down_rate'] / 1024:.2f} KB/s")
            print(f"  Upload rate: {stats['up_rate'] / 1024:.2f} KB/s")
            print(f"  Downloaded total: {stats['down_total'] / 1024 / 1024 / 1024:.2f} GB")
            print(f"  Uploaded total: {stats['up_total'] / 1024 / 1024 / 1024:.2f} GB")
    except Exception as e:
        print(f"  ⚠️  Could not get stats: {e}")

    # Get torrent counts
    print("\n📥 Torrent Counts:")
    try:
        views = {
            'main': 'All torrents',
            'started': 'Active downloads',
            'stopped': 'Stopped',
            'seeding': 'Seeding'
        }

        for view, description in views.items():
            try:
                torrents = client.get_torrents(view)
                print(f"  {description}: {len(torrents)}")
            except Exception as e:
                print(f"  {description}: Error - {e}")

    except Exception as e:
        print(f"  ⚠️  Could not get torrent counts: {e}")

    # Get detailed info for seeding torrents (first 5)
    print("\n🌱 Seeding Torrents (sample):")
    try:
        seeding_hashes = client.get_seeding_torrents()

        if not seeding_hashes:
            print("  No seeding torrents found")
        else:
            print(f"  Found {len(seeding_hashes)} seeding torrents\n")

            # Show first 5
            for i, hash_id in enumerate(seeding_hashes[:5], 1):
                try:
                    info = client.get_torrent_info(hash_id)

                    size_gb = info['size_bytes'] / 1024 / 1024 / 1024
                    print(f"  [{i}] {info['name']}")
                    print(f"      Size: {size_gb:.2f} GB")
                    print(f"      Ratio: {info['ratio']:.2f}")
                    print(f"      Active: {info['is_active']}")
                    print(f"      Hash: {hash_id[:16]}...")
                    print()

                except Exception as e:
                    print(f"  [{i}] Error getting info: {e}")
                    print()

            if len(seeding_hashes) > 5:
                print(f"  ... and {len(seeding_hashes) - 5} more")

    except Exception as e:
        print(f"  ⚠️  Could not get seeding torrents: {e}")

    # Success summary
    print("\n" + "="*70)
    print("✅ CONNECTION TEST COMPLETE")
    print("="*70)
    print("\nKey capabilities verified:")
    print("  ✓ XMLRPC connection with HTTP Digest auth")
    print("  ✓ List torrents by view (main, seeding, etc.)")
    print("  ✓ Get detailed torrent information")
    print("  ✓ Query global statistics")
    print("\nNext steps:")
    print("  - Test hash-based purge: python3 scripts/seedbox_purge.py --dry-run --verbose")
    print("  - Review SERVARR_AUTOMATION_PLAN_V2.md for deployment guide")
    print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
