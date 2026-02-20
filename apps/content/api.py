import threading
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db.models import F
from .models import PDFUpload
from .services import background_worker
from .github_control import disable_cron


@csrf_exempt
def github_trigger_worker(request):
    token = request.GET.get('token')
    if token != settings.GITHUB_TRIGGER_TOKEN:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    pdf_obj = PDFUpload.objects.filter(
        total_pages__gt=0,
        last_processed_page__lt=F('total_pages')
    ).order_by('id').first()

    if not pdf_obj:
        print("🏁 Queue empty. Disabling GitHub Cron...")
        t_disable = threading.Thread(target=disable_cron, daemon=True)
        t_disable.start()

        return JsonResponse({
            'status': 'No pending PDFs found. GitHub Cron disabled.',
            'processing_ids': []
        })

    if pdf_obj.is_processing:
        print(f"⚠️ PDF {pdf_obj.id} is stuck processing. Disabling cron to prevent loops...")
        t_disable = threading.Thread(target=disable_cron, daemon=True)
        t_disable.start()

        return JsonResponse({
            'status': f'PDF {pdf_obj.id} stuck. Cron disabled for safety.',
            'processing_ids': []
        })

    PDFUpload.objects.filter(id=pdf_obj.id).update(is_processing=True)

    batch_size = 5
    t = threading.Thread(
        target=background_worker,
        args=([pdf_obj.id], batch_size),
        daemon=True
    )
    t.start()

    return JsonResponse({
        'status': 'Worker started in background',
        'processing_id': pdf_obj.id,
        'batch_size': batch_size
    })
