import requests
from django.conf import settings

from apps.content.constants import GITHUB_API_BASE


def set_workflow_state(state: str) -> bool:
    """
    State must be 'enable' or 'disable'
    """
    # Lazy import to avoid circular dependency with models.py
    from apps.content.models import SystemConfig

    config = SystemConfig.get_solo()
    should_be_active = (state == 'enable')
    if config.is_cron_active == should_be_active:
        print(f"ℹ️ GitHub Cron is already {state}d (according to DB). Skipping API call.")
        return True

    url = f"{GITHUB_API_BASE}/repos/{settings.GITHUB_USERNAME}/{settings.GITHUB_REPO}/actions/workflows/{settings.GITHUB_WORKFLOW_ID}/{state}"
    headers = {
        "Authorization": f"token {settings.GITHUB_PAT}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        # GitHub uses PUT for enabling/disabling
        response = requests.put(url, headers=headers)

        if response.status_code == 204:
            print(f"✅ GitHub Workflow successfully {state}d.")

            # Update DB state
            config.is_cron_active = should_be_active
            config.save(update_fields=['is_cron_active'])
            return True
        else:
            print(f"❌ Failed to {state} workflow: {response.status_code} {response.text}")
            return False
    except Exception as e:
        print(f"❌ Network error contacting GitHub: {e}")
        return False


def enable_cron() -> bool:
    return set_workflow_state("enable")


def disable_cron() -> bool  :
    return set_workflow_state("disable")
