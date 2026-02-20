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

    # Automatically find PDFs that have pages, aren't locked, and aren't finished
    pending_pdfs = PDFUpload.objects.filter(
        total_pages__gt=0,
        is_processing=False,
        last_processed_page__lt=F('total_pages')
    )

    valid_ids = list(pending_pdfs.values_list('id', flat=True))

    # 3. SWITCH OFF: If the queue is empty, disable the GitHub Cron!
    if not valid_ids:
        print("🏁 Queue empty. Disabling GitHub Cron...")
        # Run in a thread so we don't delay the HTTP response
        t_disable = threading.Thread(target=disable_cron, daemon=True)
        t_disable.start()

        return JsonResponse({
            'status': 'No pending PDFs found. GitHub Cron disabled.',
            'processing_ids': []
        })

    # 4. LOCK THE PDFs: Mark them as processing so they aren't double-processed
    PDFUpload.objects.filter(id__in=valid_ids).update(is_processing=True)

    # 5. START WORKER: Exactly like your admin _process_batch logic
    batch_size = 5
    t = threading.Thread(
        target=background_worker,
        args=(valid_ids, batch_size),
        daemon=True
    )
    t.start()

    return JsonResponse({
        'status': 'Worker started in background',
        'processing_ids': valid_ids,
        'batch_size': batch_size
    })
