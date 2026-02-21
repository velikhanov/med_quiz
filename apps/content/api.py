from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db.models import F

from apps.content.services import launch_detached_worker

from .models import PDFUpload
from .github_control import disable_cron


@csrf_exempt
def github_trigger_worker(request: HttpRequest) -> JsonResponse:
    token = request.GET.get('token')
    if token != settings.GITHUB_TRIGGER_TOKEN:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    pdf_obj = PDFUpload.objects.filter(
        total_pages__gt=0,
        last_processed_page__lt=F('total_pages')
    ).order_by('id').first()

    if not pdf_obj:
        print('🏁 Queue empty. Disabling GitHub Cron...')
        # If disable_cron is fast, call it directly. If it's slow, put it in a subprocess too.
        disable_cron()
        return JsonResponse({'status': 'No pending PDFs found. GitHub Cron disabled.'})

    if pdf_obj.is_processing:
        print(f'⚠️ PDF {pdf_obj.id} is stuck processing. Disabling cron to prevent loops...')
        disable_cron()
        return JsonResponse({'status': f'PDF {pdf_obj.id} stuck. Cron disabled for safety.'})

    # Lock the PDF
    PDFUpload.objects.filter(id=pdf_obj.id).update(is_processing=True)

    batch_size = 5
    launch_detached_worker(pdf_ids=[pdf_obj.id], batch_size=batch_size)

    return JsonResponse({
        'status': 'Detached worker started successfully',
        'processing_id': pdf_obj.id,
        'batch_size': batch_size,
        'progress': f'{min(pdf_obj.last_processed_page + batch_size, pdf_obj.total_pages)}/{pdf_obj.total_pages}'
    })
