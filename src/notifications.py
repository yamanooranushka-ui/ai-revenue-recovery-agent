"""
notifications.py (Phase 2)
Mock notification sender.

The agent NEVER sends real SMS (or email, or anything else).
MockNotifier only records the notifications the agent *would*
send, so dry runs and tests are completely safe. Real SMS
delivery requires the CRM integration; the phone-number source
is NOT_YET_SPECIFIED.
"""

import uuid
from datetime import date
from typing import List, Optional

from .models import Notification


class MockNotifier:
    """Records notifications in memory. No network, no SMS, no email."""

    def __init__(self) -> None:
        self.created: List[Notification] = []

    def create(
        self,
        case_id: str,
        customer_id: str,
        message: str,
        scheduled_date: Optional[date] = None,
    ) -> Notification:
        """Record one notification. This never delivers anything."""
        notification = Notification(
            notification_id=f"NOT-{uuid.uuid4().hex[:8].upper()}",
            case_id=case_id,
            customer_id=customer_id,
            channel="SMS",
            message=message,
            scheduled_date=scheduled_date,
        )
        self.created.append(notification)
        return notification

    @property
    def count(self) -> int:
        """How many notifications have been created (not delivered)."""
        return len(self.created)
