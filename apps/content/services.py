import os
import re
import json
import base64
import subprocess
from time import sleep

from django.conf import settings
from django.db import close_old_connections, transaction, OperationalError
import fitz

from apps.content.constants import MAX_FILE_SIZE
from apps.content.groq_client import GroqClient
from apps.content.models import PDFUpload, Question
from apps.content.parsers import parse_and_save_questions


def process_next_batch(pdf: PDFUpload, batch_size: int) -> str:
    buffer = pdf.incomplete_question_data
    current_subcat_state = pdf.current_subcategory

    doc = fitz.open(pdf.file.path)
    start_page = pdf.last_processed_page

    if pdf.total_pages != len(doc):
        pdf.total_pages = len(doc)
        pdf.save(update_fields=['total_pages'])

    groq = GroqClient()
    total_created = 0

    for i in range(batch_size):
        page_num = start_page + i
        if page_num >= len(doc):
            break

        try:
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes('png')
            base64_image = base64.b64encode(img_bytes).decode('utf-8')

            # 1. External API Call (Take your time, no DB lock here)
            response = groq.get_quiz_content_from_image(base64_image)

            if response:
                # Clean up potential markdown formatting or conversational filler
                # Find the first '[' and the last ']'
                json_match = re.search(r'\[.*\]', response, re.DOTALL)
                if json_match:
                    response_cleaned = json_match.group(0)
                else:
                    response_cleaned = response

                response_json = json.loads(response_cleaned)

                # 2. Database Operations with Retry Logic
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        # Ensure we have a fresh connection before starting DB work
                        close_old_connections()

                        with transaction.atomic():
                            # Parsing logic reads/writes to DB, so it must be inside the atomic block
                            # to prevent partial updates if the subsequent bulk_create or pdf.save fails.
                            buffer, count, current_subcat_state, questions_to_create, questions_to_update = parse_and_save_questions(
                                pdf, response_json, buffer, current_subcat_state, page_num + 1
                            )

                            if questions_to_create:
                                Question.objects.bulk_create(questions_to_create)
                                total_created += count

                            if questions_to_update:
                                Question.objects.bulk_update(questions_to_update, ['explanation', 'text', 'options', 'correct_option'])

                            # Save progress inside the transaction to ensure consistency
                            pdf.last_processed_page = page_num + 1
                            pdf.incomplete_question_data = buffer
                            pdf.current_subcategory = current_subcat_state
                            pdf.save(update_fields=['last_processed_page', 'incomplete_question_data', 'current_subcategory'])

                        # Success - exit retry loop
                        break

                    except OperationalError:
                        print(f"⚠️ DB Connection lost on Page {page_num} (Attempt {attempt+1}/{max_retries}). Retrying in 2s...")
                        close_old_connections()
                        sleep(2)
                    except Exception as e:
                        # Non-recoverable error (e.g. logic error, integrity error)
                        print(f"❌ Error saving questions on Page {page_num}: {e}")
                        # Don't retry logic errors
                        break
                else:
                    print(f"⏭️ Skipping Page {page_num} after {max_retries} failed database attempts.")

        except json.JSONDecodeError:
            print(f"Error decoding JSON on page {page_num}")
        except Exception as e:
            print(f"Error processing page {page_num}: {e}")

        sleep(3)

    return f"Processed pages {start_page} to {pdf.last_processed_page}. Added {total_created} questions."


def background_worker(pdf_ids: list[int], batch_size: int) -> None:
    print(f"--- 🚀 Starting Background Batch (Count: {len(pdf_ids)}) ---", flush=True)

    for pdf_id in pdf_ids:
        try:
            # Re-fetch PDF to ensure fresh state
            close_old_connections()
            pdf = PDFUpload.objects.get(id=pdf_id)
            print(f"▶️ Processing: {pdf.title}...", flush=True)

            result = process_next_batch(pdf, batch_size)
            print(f"✅ Finished {pdf.title}: {result}", flush=True)

        except PDFUpload.DoesNotExist:
            print(f"❌ Error: PDF {pdf_id} not found.", flush=True)
        except Exception as e:
            print(f"❌ Error processing PDF {pdf_id}: {e}", flush=True)
        finally:
            try:
                close_old_connections()
                # Use a fresh fetch to unlock, just in case
                PDFUpload.objects.filter(id=pdf_id).update(is_processing=False)
                print(f"🔓 [BG] Unlocked PDF {pdf_id}", flush=True)
            except Exception as e:
                print(f"💀 [BG] Critical Error: Could not unlock PDF {pdf_id}: {e}", flush=True)

    print("--- 🏁 Batch Complete ---", flush=True)


def launch_detached_worker(pdf_ids: list[int], batch_size: int = 10):
    """
    Spawns an independent OS-level process to run the PDF batch.
    """
    log_path = os.path.join(settings.BASE_DIR, 'parser_bg.log')

    if os.path.exists(log_path) and os.path.getsize(log_path) > MAX_FILE_SIZE:
        backup_path = f"{log_path}.old"
        if os.path.exists(backup_path):
            os.remove(backup_path)

        os.rename(log_path, backup_path)

    manage_py_path = os.path.join(settings.BASE_DIR, 'manage.py')
    id_strs = [str(pid) for pid in pdf_ids]

    command = ['python', manage_py_path, 'process_pdf_batch'] + id_strs + ['--batch_size', str(batch_size)]

    with open(log_path, 'a') as log_file:
        subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            cwd=settings.BASE_DIR
        )
