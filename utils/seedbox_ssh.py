"""SSH client for seedbox management with password authentication.

This module provides SSH/SFTP functionality for interacting with the seedbox.
Uses password authentication (no SSH key required).

Example:
    >>> from utils.seedbox_ssh import SeedboxSSH
    >>> with SeedboxSSH(host, port, user, password) as ssh:
    ...     files = ssh.list_files('/downloads', older_than_days=2)
    ...     for f in files:
    ...         ssh.delete_file(f['path'])
"""

import paramiko
from typing import List, Dict, Optional
import logging
from datetime import datetime, timedelta
import os


class SeedboxError(Exception):
    """Raised when seedbox operation fails."""
    pass


class SeedboxSSH:
    """SSH/SFTP client for seedbox operations with password auth.

    This class provides a context manager for SSH connections to the seedbox
    using password authentication (no SSH key needed).

    Attributes:
        host: Seedbox hostname
        port: SSH port
        username: SSH username
        password: SSH password
        client: Paramiko SSH client
        sftp: Paramiko SFTP client
    """

    def __init__(self, host: str, port: int, username: str, password: str):
        """Initialize seedbox SSH client.

        Args:
            host: Seedbox hostname or IP
            port: SSH port (usually 40685 for dediseedbox)
            username: SSH username
            password: SSH password

        Example:
            >>> ssh = SeedboxSSH('185.56.20.18', 40685, 'ronz0', 'password')
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password

        self.client: Optional[paramiko.SSHClient] = None
        self.sftp: Optional[paramiko.SFTPClient] = None
        self.logger = logging.getLogger(__name__)

    def connect(self) -> None:
        """Establish SSH connection with password authentication.

        Raises:
            SeedboxError: If connection fails
        """
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.logger.info(f"Connecting to {self.host}:{self.port} as {self.username}")

            self.client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=30,
                look_for_keys=False,  # Don't look for SSH keys
                allow_agent=False     # Don't use SSH agent
            )

            self.sftp = self.client.open_sftp()
            self.logger.info("SSH connection established")

        except paramiko.AuthenticationException:
            raise SeedboxError("Authentication failed - check username/password")
        except paramiko.SSHException as e:
            raise SeedboxError(f"SSH connection failed: {e}")
        except Exception as e:
            raise SeedboxError(f"Failed to connect to seedbox: {e}")

    def disconnect(self) -> None:
        """Close SSH connection."""
        if self.sftp:
            self.sftp.close()
            self.sftp = None

        if self.client:
            self.client.close()
            self.client = None

        self.logger.info("SSH connection closed")

    def __enter__(self):
        """Context manager entry - establish connection."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.disconnect()

    def execute_command(self, command: str) -> tuple[str, str, int]:
        """Execute a shell command on the seedbox.

        Args:
            command: Shell command to execute

        Returns:
            Tuple of (stdout, stderr, exit_code)

        Raises:
            SeedboxError: If not connected or command fails
        """
        if not self.client:
            raise SeedboxError("Not connected to seedbox")

        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            exit_code = stdout.channel.recv_exit_status()

            stdout_text = stdout.read().decode('utf-8')
            stderr_text = stderr.read().decode('utf-8')

            return stdout_text, stderr_text, exit_code

        except Exception as e:
            raise SeedboxError(f"Command execution failed: {e}")

    def list_files(
        self,
        path: str,
        older_than_days: Optional[int] = None,
        pattern: Optional[str] = None
    ) -> List[Dict[str, any]]:
        """List files in a directory on the seedbox.

        Args:
            path: Directory path on seedbox
            older_than_days: Only include files older than this many days
            pattern: Optional filename pattern (e.g., '*.mkv')

        Returns:
            List of file dictionaries with keys: path, size, mtime

        Example:
            >>> files = ssh.list_files('/downloads', older_than_days=2)
            >>> for f in files:
            ...     print(f"{f['path']}: {f['size']} bytes")
        """
        if not self.client:
            raise SeedboxError("Not connected to seedbox")

        # Build find command
        find_cmd = f'find "{path}" -type f'

        if older_than_days is not None:
            find_cmd += f' -mtime +{older_than_days}'

        if pattern:
            find_cmd += f' -name "{pattern}"'

        find_cmd += ' -printf "%p\\t%s\\t%T@\\n"'

        stdout, stderr, exit_code = self.execute_command(find_cmd)

        if exit_code != 0:
            self.logger.warning(f"find command failed: {stderr}")
            return []

        files = []
        for line in stdout.strip().split('\n'):
            if not line:
                continue

            parts = line.split('\t')
            if len(parts) != 3:
                continue

            file_path, size_str, mtime_str = parts

            try:
                files.append({
                    'path': file_path,
                    'size': int(size_str),
                    'mtime': float(mtime_str)
                })
            except ValueError:
                self.logger.warning(f"Failed to parse file info: {line}")
                continue

        return files

    def get_file_size(self, path: str) -> int:
        """Get size of a file on the seedbox.

        Args:
            path: File path on seedbox

        Returns:
            File size in bytes

        Raises:
            SeedboxError: If file doesn't exist or can't be accessed
        """
        if not self.sftp:
            raise SeedboxError("SFTP not connected")

        try:
            stat = self.sftp.stat(path)
            return stat.st_size
        except FileNotFoundError:
            raise SeedboxError(f"File not found: {path}")
        except Exception as e:
            raise SeedboxError(f"Failed to get file size: {e}")

    def delete_file(self, path: str) -> bool:
        """Delete a file on the seedbox.

        Args:
            path: File path on seedbox

        Returns:
            True if deleted successfully

        Raises:
            SeedboxError: If deletion fails
        """
        if not self.client:
            raise SeedboxError("Not connected to seedbox")

        # Use rm command for safer deletion
        stdout, stderr, exit_code = self.execute_command(f'rm -f "{path}"')

        if exit_code != 0:
            raise SeedboxError(f"Failed to delete {path}: {stderr}")

        self.logger.debug(f"Deleted: {path}")
        return True

    def delete_empty_directories(self, path: str, exclude_paths: Optional[List[str]] = None) -> int:
        """Delete empty directories recursively, respecting protected folders.

        Args:
            path: Base directory path
            exclude_paths: List of protected folder paths (e.g., ["/_ready", "/.recycle"])
                          These directories will never be deleted, even if empty

        Returns:
            Number of directories deleted

        Example:
            >>> ssh.delete_empty_directories('/downloads', exclude_paths=['/_ready'])
            # Will delete empty subdirectories but preserve /_ready folder itself
        """
        if not self.client:
            raise SeedboxError("Not connected to seedbox")

        if exclude_paths is None:
            exclude_paths = []

        # Find all empty directories
        find_cmd = f'find "{path}" -type d -empty -print'
        stdout, stderr, exit_code = self.execute_command(find_cmd)

        if exit_code != 0:
            self.logger.warning(f"find command failed: {stderr}")
            return 0

        # Parse found directories
        empty_dirs = [d.strip() for d in stdout.strip().split('\n') if d.strip()]

        if not empty_dirs:
            return 0

        # Filter out protected directories
        dirs_to_delete = []
        protected_count = 0

        for dir_path in empty_dirs:
            # Check if this directory is protected
            is_protected = False
            for protected in exclude_paths:
                # Match if directory path ends with protected path
                # e.g., "/downloads/_ready" matches "/_ready"
                if dir_path.endswith(protected) or dir_path == protected:
                    is_protected = True
                    protected_count += 1
                    self.logger.debug(f"Skipping protected folder: {dir_path} (matches {protected})")
                    break

            if not is_protected:
                dirs_to_delete.append(dir_path)

        # Log protection summary
        if protected_count > 0:
            self.logger.info(f"Protected {protected_count} folder(s) from deletion")

        # Delete non-protected directories
        deleted_count = 0
        for dir_path in dirs_to_delete:
            try:
                # Use rmdir (only removes if empty, safer than rm -rf)
                cmd = f'rmdir "{dir_path}"'
                _, stderr, exit_code = self.execute_command(cmd)

                if exit_code == 0:
                    deleted_count += 1
                    self.logger.debug(f"Deleted empty directory: {dir_path}")
                else:
                    self.logger.debug(f"Could not delete {dir_path}: {stderr}")

            except Exception as e:
                self.logger.debug(f"Error deleting {dir_path}: {e}")

        return deleted_count

    def get_disk_usage(self) -> Dict[str, float]:
        """Get disk usage statistics for the seedbox.

        Returns:
            Dictionary with keys: total_gb, used_gb, available_gb, percent_used

        Example:
            >>> usage = ssh.get_disk_usage()
            >>> print(f"Used: {usage['used_gb']:.1f} GB / {usage['total_gb']:.1f} GB")
        """
        if not self.client:
            raise SeedboxError("Not connected to seedbox")

        # Use df command to get disk usage
        stdout, stderr, exit_code = self.execute_command('df -BG /')

        if exit_code != 0:
            raise SeedboxError(f"Failed to get disk usage: {stderr}")

        lines = stdout.strip().split('\n')
        if len(lines) < 2:
            raise SeedboxError("Unexpected df output")

        # Parse df output (skip header line)
        fields = lines[1].split()
        if len(fields) < 5:
            raise SeedboxError("Unexpected df output format")

        # Extract values (remove 'G' suffix and convert to float)
        total_gb = float(fields[1].rstrip('G'))
        used_gb = float(fields[2].rstrip('G'))
        available_gb = float(fields[3].rstrip('G'))
        percent_used = float(fields[4].rstrip('%'))

        return {
            'total_gb': total_gb,
            'used_gb': used_gb,
            'available_gb': available_gb,
            'percent_used': percent_used
        }

    def path_exists(self, path: str) -> bool:
        """Check if a path exists on the seedbox.

        Args:
            path: Path to check

        Returns:
            True if path exists
        """
        if not self.sftp:
            raise SeedboxError("SFTP not connected")

        try:
            self.sftp.stat(path)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False


# Example usage
if __name__ == '__main__':
    import sys

    print("Testing seedbox SSH connection...")
    print("(Configure credentials in config.yaml first)\n")

    from utils.config_loader import load_config

    try:
        config = load_config('config.yaml')
        sb_config = config['seedbox']

        with SeedboxSSH(
            host=sb_config['host'],
            port=sb_config['port'],
            username=sb_config['username'],
            password=sb_config['password']
        ) as ssh:
            print("✓ Connected to seedbox\n")

            # Test disk usage
            print("Disk Usage:")
            usage = ssh.get_disk_usage()
            print(f"  Used: {usage['used_gb']:.1f} GB / {usage['total_gb']:.1f} GB ({usage['percent_used']:.1f}%)")
            print(f"  Available: {usage['available_gb']:.1f} GB\n")

            # Test file listing
            print("Files in /downloads (older than 2 days):")
            files = ssh.list_files('/downloads', older_than_days=2)
            print(f"  Found {len(files)} files\n")

            if files:
                for f in files[:5]:  # Show first 5
                    size_gb = f['size'] / (1024 ** 3)
                    print(f"  - {os.path.basename(f['path'])} ({size_gb:.2f} GB)")

                if len(files) > 5:
                    print(f"  ... and {len(files) - 5} more")

            print("\n✓ Seedbox SSH client working!")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
