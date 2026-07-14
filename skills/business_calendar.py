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

    # Days before hire_date to send the manager notification, by weekday
    # Monday=0 ... Friday=4
    DAYS_BACK = {0: 4, 1: 4, 2: 5, 3: 3, 4: 3}

    def _run(self, hire_date: str) -> dict:
        try:
            hd = datetime.strptime(hire_date, "%Y-%m-%d")
        except ValueError:
            return {
                "success": False,
                "error": f"Invalid hire_date format: {hire_date}. Expected YYYY-MM-DD.",
            }

        weekday = hd.weekday()
        days_back = self.DAYS_BACK.get(weekday, 3)
        send_date = hd - timedelta(days=days_back)

        days_until_hire = (hd - datetime.today()).days

        return {
            "result": {
                "hireDate": hire_date,
                "managerNotifyDate": send_date.strftime("%Y-%m-%d"),
                "managerNotifyFormatted": send_date.strftime("%d/%m/%Y"),
                "daysBeforeHire": days_back,
                "daysUntilHire": days_until_hire,
            },
            "decision": f"Notify manager on {send_date.strftime('%d/%m/%Y')} ({days_back} days before hire date)",
            "reasoning": (
                f"Employee starts on {hd.strftime('%A %d/%m/%Y')} (weekday={weekday}). "
                f"Rule: notify {days_back} business days before to give manager enough preparation time."
            ),
        }


def get_manager_send_date(hire_date: datetime) -> datetime:
    """Standalone helper for backward compatibility."""
    skill = BusinessCalendarSkill()
    result = skill.execute(hire_date=hire_date.strftime("%Y-%m-%d"))
    return datetime.strptime(result["result"]["managerNotifyDate"], "%Y-%m-%d")
