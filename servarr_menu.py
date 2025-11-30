#!/usr/bin/env python3
"""Servarr Automation Suite - Interactive CLI Menu.

This is the main entry point for the Servarr automation suite. It provides
an interactive menu for running all scripts, viewing logs, and managing
configuration.

Features:
- Interactive menu for all automation scripts
- Dry-run toggle (persists during session)
- Real-time script output
- Log viewer (tail -f style)
- Config display (with secrets masked)
- Run all daily tasks option
- Error handling and confirmation prompts

Usage:
    python3 servarr_menu.py
"""

import sys
import os
import subprocess
from pathlib import Path
from typing import Optional
import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from utils.config_loader import load_config, mask_secrets
from utils.logger import setup_logging


class ServarrMenu:
    """Interactive CLI menu for Servarr automation suite."""

    def __init__(self):
        """Initialize menu."""
        self.dry_run = True  # Start in safe mode
        self.config = None
        self.scripts_dir = Path(__file__).parent / 'scripts'
        self.logs_dir = None

        # Try to load config
        try:
            self.config = load_config('config.yaml')
            self.logs_dir = Path(self.config['paths']['logs'])
        except Exception as e:
            print(f"Warning: Could not load config: {e}")
            print("Some features may not work.\n")

    def clear_screen(self):
        """Clear the terminal screen."""
        os.system('clear' if os.name != 'nt' else 'cls')

    def print_header(self):
        """Print menu header."""
        self.clear_screen()
        print("╔" + "="*58 + "╗")
        print("║" + " "*15 + "SERVARR AUTOMATION SUITE v1.0" + " "*14 + "║")
        print("╠" + "="*58 + "╣")

    def print_menu(self):
        """Print main menu."""
        self.print_header()

        dry_run_status = "ON " if self.dry_run else "OFF"
        dry_run_color = "\033[92m" if self.dry_run else "\033[91m"  # Green if ON, Red if OFF
        reset_color = "\033[0m"

        print("║  AUTOMATION SCRIPTS" + " "*39 + "║")
        print("║  1. Seedbox Sync          - Download from seedbox" + " "*9 + "║")
        print("║  2. Seedbox Purge         - Clean old seedbox files" + " "*7 + "║")
        print("║  3. Video Cleanup         - Remove extras/trailers" + " "*8 + "║")
        print("║  4. Jellyfin Notify       - Update Jellyfin library" + " "*7 + "║")
        print("║  5. Library Analyzer      - Analyze watch patterns" + " "*8 + "║")
        print("║  6. Library Reducer       - Tag items for deletion" + " "*7 + "║")
        print("║" + " "*60 + "║")
        print("║  UTILITIES" + " "*48 + "║")
        print("║  7. View Logs             - Browse execution logs" + " "*9 + "║")
        print("║  8. View Config           - Display configuration" + " "*8 + "║")
        print("║  9. Run All (Daily)       - Execute daily tasks" + " "*11 + "║")
        print("║" + " "*60 + "║")
        print(f"║  D. Toggle Dry-Run Mode   - Currently: [{dry_run_color}{dry_run_status}{reset_color}]" + " "*15 + "║")
        print("║  Q. Quit" + " "*50 + "║")
        print("╚" + "="*58 + "╝")
        print()

    def run_script(self, script_name: str, description: str, extra_args: list = None) -> bool:
        """Run a script and display output.

        Args:
            script_name: Name of the script file
            description: Description for logging
            extra_args: Additional arguments to pass

        Returns:
            True if successful, False otherwise
        """
        script_path = self.scripts_dir / script_name

        if not script_path.exists():
            print(f"\nERROR: Script not found: {script_path}")
            input("\nPress Enter to continue...")
            return False

        # Build command
        mode_flag = '--dry-run' if self.dry_run else '--execute'
        cmd = ['python3', str(script_path), mode_flag]

        if extra_args:
            cmd.extend(extra_args)

        # Show what we're doing
        self.clear_screen()
        print("="*60)
        print(f"RUNNING: {description}")
        print("="*60)
        print(f"Mode: {'DRY-RUN (safe)' if self.dry_run else 'EXECUTE (live)'}")
        print(f"Command: {' '.join(cmd)}")
        print("="*60)
        print()

        # Confirm if in execute mode
        if not self.dry_run:
            confirm = input("⚠️  EXECUTE MODE - Continue? (yes/no): ").strip().lower()
            if confirm not in ['yes', 'y']:
                print("\nCancelled.")
                input("\nPress Enter to continue...")
                return False
            print()

        # Run script with real-time output
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )

            # Stream output
            for line in process.stdout:
                print(line, end='')

            # Wait for completion
            process.wait()

            print("\n" + "="*60)
            if process.returncode == 0:
                print("✓ Script completed successfully")
            else:
                print(f"✗ Script failed with exit code {process.returncode}")
            print("="*60)

            input("\nPress Enter to continue...")
            return process.returncode == 0

        except KeyboardInterrupt:
            print("\n\n⚠️  Script interrupted by user")
            input("\nPress Enter to continue...")
            return False
        except Exception as e:
            print(f"\n\nERROR: {e}")
            input("\nPress Enter to continue...")
            return False

    def view_logs(self):
        """Interactive log viewer."""
        if not self.logs_dir or not self.logs_dir.exists():
            print("\nERROR: Logs directory not found")
            input("\nPress Enter to continue...")
            return

        while True:
            self.clear_screen()
            print("="*60)
            print("LOG VIEWER")
            print("="*60)

            # List available logs
            log_files = sorted(self.logs_dir.glob('*.log'))

            if not log_files:
                print("\nNo log files found.")
                input("\nPress Enter to return to menu...")
                return

            print("\nAvailable logs:")
            for i, log_file in enumerate(log_files, 1):
                size_kb = log_file.stat().st_size / 1024
                print(f"  {i}. {log_file.name} ({size_kb:.1f} KB)")

            print("\n  B. Back to menu")

            choice = input("\nSelect log to view (or B to go back): ").strip()

            if choice.upper() == 'B':
                return

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(log_files):
                    log_file = log_files[idx]
                    self.display_log(log_file)
                else:
                    print("\nInvalid selection")
                    input("\nPress Enter to continue...")
            except ValueError:
                print("\nInvalid input")
                input("\nPress Enter to continue...")

    def display_log(self, log_file: Path):
        """Display a log file with options.

        Args:
            log_file: Path to log file
        """
        while True:
            self.clear_screen()
            print("="*60)
            print(f"LOG: {log_file.name}")
            print("="*60)
            print("\n1. View last 50 lines")
            print("2. View last 100 lines")
            print("3. View entire file")
            print("4. Tail -f (follow)")
            print("B. Back")

            choice = input("\nSelect option: ").strip()

            if choice.upper() == 'B':
                return
            elif choice == '1':
                self.show_log_lines(log_file, 50)
            elif choice == '2':
                self.show_log_lines(log_file, 100)
            elif choice == '3':
                self.show_log_lines(log_file, None)
            elif choice == '4':
                self.tail_log(log_file)

    def show_log_lines(self, log_file: Path, lines: Optional[int]):
        """Show last N lines of log file.

        Args:
            log_file: Path to log file
            lines: Number of lines to show (None = all)
        """
        self.clear_screen()
        print("="*60)
        print(f"LOG: {log_file.name}")
        if lines:
            print(f"(Last {lines} lines)")
        print("="*60)
        print()

        try:
            if lines:
                cmd = ['tail', '-n', str(lines), str(log_file)]
            else:
                cmd = ['cat', str(log_file)]

            subprocess.run(cmd)
            print()
        except Exception as e:
            print(f"\nERROR: {e}")

        input("\nPress Enter to continue...")

    def tail_log(self, log_file: Path):
        """Tail -f a log file.

        Args:
            log_file: Path to log file
        """
        self.clear_screen()
        print("="*60)
        print(f"TAIL: {log_file.name}")
        print("(Press Ctrl+C to stop)")
        print("="*60)
        print()

        try:
            subprocess.run(['tail', '-f', str(log_file)])
        except KeyboardInterrupt:
            print("\n\nStopped tailing log.")
        except Exception as e:
            print(f"\nERROR: {e}")

        input("\nPress Enter to continue...")

    def view_config(self):
        """Display configuration with secrets masked."""
        self.clear_screen()
        print("="*60)
        print("CONFIGURATION")
        print("="*60)
        print()

        if not self.config:
            print("ERROR: Configuration not loaded")
            input("\nPress Enter to continue...")
            return

        # Mask secrets
        safe_config = mask_secrets(self.config)

        # Pretty print YAML
        try:
            yaml_str = yaml.dump(safe_config, default_flow_style=False, sort_keys=False)
            print(yaml_str)
        except Exception as e:
            print(f"ERROR: {e}")

        print("\n" + "="*60)
        print("Note: Secrets are masked with '****'")
        print("="*60)
        input("\nPress Enter to continue...")

    def run_all_daily(self):
        """Run all daily tasks in sequence."""
        self.clear_screen()
        print("="*60)
        print("RUN ALL DAILY TASKS")
        print("="*60)
        print()
        print("This will run the following scripts in order:")
        print("  1. Seedbox Sync")
        print("  2. Seedbox Purge")
        print("  3. Jellyfin Notify")
        print()
        print(f"Mode: {'DRY-RUN' if self.dry_run else 'EXECUTE'}")
        print()

        if not self.dry_run:
            confirm = input("⚠️  EXECUTE MODE - Continue? (yes/no): ").strip().lower()
            if confirm not in ['yes', 'y']:
                print("\nCancelled.")
                input("\nPress Enter to continue...")
                return

        # Run scripts in sequence
        scripts = [
            ('seedbox_sync.py', 'Seedbox Sync'),
            ('seedbox_purge.py', 'Seedbox Purge'),
            ('jellyfin_notify.py', 'Jellyfin Notify'),
        ]

        results = []
        for script, desc in scripts:
            print(f"\n{'='*60}")
            print(f"Running: {desc}")
            print('='*60)
            success = self.run_script(script, desc)
            results.append((desc, success))

        # Summary
        self.clear_screen()
        print("="*60)
        print("DAILY TASKS SUMMARY")
        print("="*60)
        print()
        for desc, success in results:
            status = "✓ SUCCESS" if success else "✗ FAILED"
            print(f"  {desc}: {status}")
        print()
        print("="*60)
        input("\nPress Enter to continue...")

    def toggle_dry_run(self):
        """Toggle dry-run mode."""
        self.dry_run = not self.dry_run
        mode = "DRY-RUN (safe)" if self.dry_run else "EXECUTE (live)"

        self.clear_screen()
        print("="*60)
        print(f"Mode changed to: {mode}")
        print("="*60)

        if not self.dry_run:
            print("\n⚠️  WARNING: EXECUTE mode will make real changes!")
            print("   - Delete files from seedbox")
            print("   - Remove video extras")
            print("   - Tag items for deletion")
            print()
        else:
            print("\n✓ DRY-RUN mode is safe - no changes will be made")
            print()

        input("Press Enter to continue...")

    def run(self):
        """Main menu loop."""
        while True:
            self.print_menu()

            choice = input("Select option: ").strip().upper()

            if choice == '1':
                self.run_script('seedbox_sync.py', 'Seedbox Sync')
            elif choice == '2':
                self.run_script('seedbox_purge.py', 'Seedbox Purge')
            elif choice == '3':
                self.run_script('video_cleanup.py', 'Video Cleanup')
            elif choice == '4':
                self.run_script('jellyfin_notify.py', 'Jellyfin Notify')
            elif choice == '5':
                self.run_script('library_analyzer.py', 'Library Analyzer')
            elif choice == '6':
                # Library reducer needs report file
                self.clear_screen()
                print("="*60)
                print("LIBRARY REDUCER")
                print("="*60)
                print()

                # List available reports
                if self.config:
                    reports_dir = Path(self.config['paths']['reports'])
                    if reports_dir.exists():
                        reports = sorted(reports_dir.glob('library_analysis_*.csv'), reverse=True)
                        if reports:
                            print("Recent analysis reports:")
                            for i, report in enumerate(reports[:5], 1):
                                print(f"  {i}. {report.name}")
                            print()

                            choice = input("Select report (1-5) or enter custom path: ").strip()

                            try:
                                if choice.isdigit():
                                    idx = int(choice) - 1
                                    if 0 <= idx < len(reports):
                                        report_path = reports[idx]
                                    else:
                                        print("\nInvalid selection")
                                        input("\nPress Enter to continue...")
                                        continue
                                else:
                                    report_path = Path(choice)
                                    if not report_path.exists():
                                        print(f"\nReport not found: {report_path}")
                                        input("\nPress Enter to continue...")
                                        continue

                                self.run_script('library_reducer.py', 'Library Reducer',
                                              ['--report', str(report_path)])
                            except Exception as e:
                                print(f"\nERROR: {e}")
                                input("\nPress Enter to continue...")
                        else:
                            print("No analysis reports found.")
                            print("Run Library Analyzer first (option 5)")
                            input("\nPress Enter to continue...")
                    else:
                        print("Reports directory not found")
                        input("\nPress Enter to continue...")
                else:
                    print("Config not loaded")
                    input("\nPress Enter to continue...")

            elif choice == '7':
                self.view_logs()
            elif choice == '8':
                self.view_config()
            elif choice == '9':
                self.run_all_daily()
            elif choice == 'D':
                self.toggle_dry_run()
            elif choice == 'Q':
                self.clear_screen()
                print("\nGoodbye!\n")
                sys.exit(0)
            else:
                print("\nInvalid option. Please try again.")
                input("\nPress Enter to continue...")


def main():
    """Main entry point."""
    try:
        menu = ServarrMenu()
        menu.run()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Goodbye!\n")
        sys.exit(0)
    except Exception as e:
        print(f"\nFATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
