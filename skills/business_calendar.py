"""
Skill: Business Calendar
Calculates the optimal date to notify the manager before an employee's first day.
"""

from datetime import datetime, timedelta
from skills.base import Skill


class BusinessCalendarSkill(Skill):
    name = "business_calendar"
    description = "Calculates the optimal date to notify a manager before an employee starts."
    category = "planning"

    # Business days (Mon-Fri, weekends skipped) before hire_date for each
    # message type — confirmed with Junior against Mon/Tue/Wed hire-date
    # examples: manager=3, email ("Tu aventura...")=2, #koronet channel=1.
    MANAGER_DAYS_BACK = 3
    EMAIL_DAYS_BACK = 2
    KORONET_DAYS_BACK = 1

    @staticmethod
    def _n_business_days_before(d: datetime, n: int) -> datetime:
        """Steps back n business days (skips Saturday/Sunday each step)."""
        cur = d
        counted = 0
        while counted < n:
            cur -= timedelta(days=1)
            if cur.weekday() < 5:  # Monday=0 ... Friday=4
                counted += 1
        return cur

    def _run(self, hire_date: str) -> dict:
        try:
            hd = datetime.strptime(hire_date, "%Y-%m-%d")
        except ValueError:
            return {
                "success": False,
                "error": f"Invalid hire_date format: {hire_date}. Expected YYYY-MM-DD.",
            }

        weekday = hd.weekday()
        send_date = self._n_business_days_before(hd, self.MANAGER_DAYS_BACK)
        email_send_date = self._n_business_days_before(hd, self.EMAIL_DAYS_BACK)
        koronet_send_date = self._n_business_days_before(hd, self.KORONET_DAYS_BACK)

        days_until_hire = (hd - datetime.today()).days

        return {
            "result": {
                "hireDate": hire_date,
                "managerNotifyDate": send_date.strftime("%Y-%m-%d"),
                "managerNotifyFormatted": send_date.strftime("%d/%m/%Y"),
                "daysBeforeHire": self.MANAGER_DAYS_BACK,
                "emailNotifyDate": email_send_date.strftime("%Y-%m-%d"),
                "emailNotifyFormatted": email_send_date.strftime("%d/%m/%Y"),
                "koronetNotifyDate": koronet_send_date.strftime("%Y-%m-%d"),
                "koronetNotifyFormatted": koronet_send_date.strftime("%d/%m/%Y"),
                "daysUntilHire": days_until_hire,
            },
            "decision": f"Notify manager on {send_date.strftime('%d/%m/%Y')} ({self.MANAGER_DAYS_BACK} business days before hire date)",
            "reasoning": (
                f"Employee starts on {hd.strftime('%A %d/%m/%Y')} (weekday={weekday}). "
                f"Rule: manager notified {self.MANAGER_DAYS_BACK} business days before, "
                f"onboarding email {self.EMAIL_DAYS_BACK} business days before, "
                f"#koronet channel welcome {self.KORONET_DAYS_BACK} business day before — "
                f"all counted skipping weekends, never calendar days."
            ),
        }


def get_manager_send_date(hire_date: datetime) -> datetime:
    """Standalone helper for backward compatibility."""
    skill = BusinessCalendarSkill()
    result = skill.execute(hire_date=hire_date.strftime("%Y-%m-%d"))
    return datetime.strptime(result["result"]["managerNotifyDate"], "%Y-%m-%d")
