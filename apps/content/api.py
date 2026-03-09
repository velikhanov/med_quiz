from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

from apps.content.services import trigger_next_pdf_batch


@csrf_exempt
def github_trigger_worker(request: HttpRequest) -> JsonResponse:
    token = request.GET.get("token")
    if token != settings.GITHUB_TRIGGER_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    result = trigger_next_pdf_batch(is_cron=True)

    if result["action"] == "cron_disabled":
        # Ensure we return a helpful status
        if "stuck" in result["status"]:
            return JsonResponse({"status": result["status"] + " Cron disabled for safety."})
        return JsonResponse({"status": "No pending PDFs found. GitHub Cron disabled."})

    return JsonResponse(result)
