import requests
from django.conf import settings

from apps.content.constants import GITHUB_API_BASE


def set_workflow_state(state: str) -> bool:
    """
    State must be 'enable' or 'disable'
    """
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
            return True
        else:
            print(f"❌ Failed to {state} workflow: {response.status_code} {response.text}")
            return False
    except Exception as e:
        print(f"❌ Network error contacting GitHub: {e}")


def enable_cron() -> bool | None:
    return set_workflow_state("enable")


def disable_cron() -> bool | None:
    return set_workflow_state("disable")
