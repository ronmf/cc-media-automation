"""ntfy.sh notification integration.

This module provides notifications via ntfy.sh for Servarr automation scripts.
Notifications are sent for errors (always) and optionally for success.

Example:
    >>> from utils.ntfy_notifier import NtfyNotifier
    >>> notifier = NtfyNotifier(config['notifications']['ntfy'])
    >>> notifier.notify_error('seedbox_sync', 'Connection failed')
"""

import requests
from typing import List, Optional, Dict, Any
import logging


class NtfyNotifier:
    """ntfy.sh notification client.

    Attributes:
        url: ntfy.sh topic URL
        priority: Default priority level
        tags: Default tags for notifications
        enabled: Whether notifications are enabled
        send_on_success: Whether to send success notifications
        send_on_error: Whether to send error notifications
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize ntfy notifier from configuration.

        Args:
            config: notifications.ntfy section from config

        Example:
            >>> config = {'url': 'https://ntfy.sh/topic', 'enabled': True}
            >>> notifier = NtfyNotifier(config)
        """
        self.url = config.get('url', '')
        self.priority = config.get('priority', 'default')
        self.tags = config.get('tags', ['servarr'])
        self.enabled = config.get('enabled', True)
        self.send_on_success = config.get('send_on_success', False)
        self.send_on_error = config.get('send_on_error', True)

        self.logger = logging.getLogger(__name__)

        if not self.url and self.enabled:
            self.logger.warning("ntfy URL not configured, notifications disabled")
            self.enabled = False

    def notify(
        self,
        title: str,
        message: str,
        priority: Optional[str] = None,
        tags: Optional[List[str]] = None,
        actions: Optional[List[Dict]] = None
    ) -> bool:
        """Send a notification to ntfy.

        Args:
            title: Notification title
            message: Notification message body
            priority: Priority level (min, low, default, high, urgent)
            tags: List of tags (emojis or keywords)
            actions: Optional action buttons

        Returns:
            True if notification sent successfully, False otherwise

        Example:
            >>> notifier.notify('Test', 'Hello world', priority='high')
            True
        """
        if not self.enabled:
            self.logger.debug("Notifications disabled, skipping")
            return False

        headers = {
            'Title': title,
            'Priority': priority or self.priority,
            'Tags': ','.join(tags or self.tags)
        }

        # Add actions if provided
        if actions:
            headers['Actions'] = '; '.join([
                f"{a['action']}, {a['label']}, {a['url']}"
                for a in actions
            ])

        try:
            response = requests.post(
                self.url,
                data=message.encode('utf-8'),
                headers=headers,
                timeout=10
            )
            response.raise_for_status()

            self.logger.debug(f"Sent ntfy notification: {title}")
            return True

        except requests.exceptions.Timeout:
            self.logger.warning("ntfy notification timed out")
            return False
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Failed to send ntfy notification: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error sending notification: {e}")
            return False

    def notify_error(
        self,
        script_name: str,
        error_message: str,
        details: Optional[str] = None
    ) -> bool:
        """Send an error notification.

        Args:
            script_name: Name of the script that failed
            error_message: Short error description
            details: Optional detailed error information

        Returns:
            True if notification sent successfully

        Example:
            >>> notifier.notify_error('seedbox_sync', 'Connection timeout',
            ...                       details='Failed to connect after 3 retries')
        """
        if not self.send_on_error:
            return False

        title = f"Servarr: {script_name} Failed"

        message = f"❌ {error_message}"
        if details:
            message += f"\n\nDetails:\n{details}"

        return self.notify(
            title=title,
            message=message,
            priority='high',
            tags=['warning', 'servarr', 'error']
        )

    def notify_success(
        self,
        script_name: str,
        summary: str,
        stats: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send a success notification.

        Args:
            script_name: Name of the script that completed
            summary: Summary of what was done
            stats: Optional statistics dictionary

        Returns:
            True if notification sent successfully

        Example:
            >>> stats = {'files': 10, 'size_gb': 25.5}
            >>> notifier.notify_success('seedbox_sync', 'Download complete', stats)
        """
        if not self.send_on_success:
            return False

        title = f"Servarr: {script_name} Complete"

        message = f"✅ {summary}"

        if stats:
            message += "\n\nStats:"
            for key, value in stats.items():
                message += f"\n  • {key}: {value}"

        return self.notify(
            title=title,
            message=message,
            priority='default',
            tags=['servarr', 'success']
        )

    def notify_warning(
        self,
        script_name: str,
        warning_message: str,
        recommendation: Optional[str] = None
    ) -> bool:
        """Send a warning notification.

        Args:
            script_name: Name of the script
            warning_message: Warning description
            recommendation: Optional recommended action

        Returns:
            True if notification sent successfully

        Example:
            >>> notifier.notify_warning('seedbox_purge', 'Disk usage at 95%',
            ...                         recommendation='Run purge manually')
        """
        title = f"Servarr: {script_name} Warning"

        message = f"⚠️ {warning_message}"
        if recommendation:
            message += f"\n\nRecommendation:\n{recommendation}"

        return self.notify(
            title=title,
            message=message,
            priority='high',
            tags=['warning', 'servarr']
        )

    def notify_info(
        self,
        script_name: str,
        info_message: str
    ) -> bool:
        """Send an informational notification.

        Args:
            script_name: Name of the script
            info_message: Information message

        Returns:
            True if notification sent successfully

        Example:
            >>> notifier.notify_info('library_analyzer', 'Analysis started')
        """
        title = f"Servarr: {script_name}"

        return self.notify(
            title=title,
            message=f"ℹ️ {info_message}",
            priority='low',
            tags=['servarr', 'info']
        )


def create_notifier(config: Dict[str, Any]) -> NtfyNotifier:
    """Factory function to create ntfy notifier from full config.

    Args:
        config: Full configuration dictionary

    Returns:
        Configured NtfyNotifier instance

    Example:
        >>> from utils.config_loader import load_config
        >>> config = load_config('config.yaml')
        >>> notifier = create_notifier(config)
    """
    return NtfyNotifier(config.get('notifications', {}).get('ntfy', {}))


# Example usage
if __name__ == '__main__':
    import sys

    # Test configuration
    test_config = {
        'url': 'https://ntfy.les7nains.com/topic_default',
        'enabled': True,
        'priority': 'default',
        'tags': ['servarr', 'test'],
        'send_on_success': True,
        'send_on_error': True
    }

    notifier = NtfyNotifier(test_config)

    print("Testing ntfy notifications...")
    print("(Check your ntfy topic for messages)\n")

    # Test error notification
    print("1. Sending error notification...")
    notifier.notify_error(
        'test_script',
        'Connection failed',
        details='This is a test error notification from the Servarr automation suite'
    )

    # Test success notification
    print("2. Sending success notification...")
    notifier.notify_success(
        'test_script',
        'Test completed successfully',
        stats={'files': 5, 'size_gb': 12.3, 'duration': '5m 23s'}
    )

    # Test warning notification
    print("3. Sending warning notification...")
    notifier.notify_warning(
        'test_script',
        'Disk usage at 90%',
        recommendation='Consider running cleanup script'
    )

    # Test info notification
    print("4. Sending info notification...")
    notifier.notify_info(
        'test_script',
        'Test notification sequence complete'
    )

    print("\nAll test notifications sent!")
    print("Check https://ntfy.les7nains.com/topic_default")
