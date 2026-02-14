import re
import json

import base64
import fitz

from django.db import close_old_connections, transaction

from apps.content.groq_client import GroqClient
from apps.content.models import PDFUpload, Question


def parse_and_save_questions(pdf, response_json, buffer, current_subcat_state, page_num):
    """
    Args:
        current_subcat_state: The active subcategory from the previous item/page.
    Returns:
        (new_buffer, count, updated_subcat_state, questions_list)
    """
    questions_to_create = []
    new_buffer = buffer
    active_subcat = current_subcat_state  # Start with what we knew last

    for item in response_json:
        if not item:
            continue

        # 1. UPDATE STATE: Did the AI find a NEW header on this page?
        if item.get('subcategory'):
            active_subcat = item['subcategory'].strip().capitalize()
        final_subcategory = active_subcat

        # 2. OPTION CLEANER: Aggressively rebuild options with A), B)...
        # (We calculate this once here, so it's ready for fragments or questions)
        raw_options = item.get('options', [])
        cleaned_options = []
        if raw_options:
            for idx, opt in enumerate(raw_options):
                opt = str(opt).strip()
                # Remove existing prefixes like "A.", "1.", "a)"
                opt_clean = re.sub(r'^([A-Za-z0-9]+[\.\)\-]\s*)', '', opt)
                # Build perfect prefix
                letter = chr(65 + idx)  # 0->A, 1->B...
                cleaned_options.append(f"{letter}) {opt_clean}")

        # ======================================================
        # --- TYPE 1: ORPHANED EXPLANATION (Fix for Q2) ---
        # ======================================================
        if item.get('type') == 'explanation_only':
            explanation_text = item.get('explanation', '')

            # Scenario A: It belongs to the last question we just parsed in THIS batch
            if questions_to_create:
                prev_q = questions_to_create[-1]
                if prev_q.explanation:
                    prev_q.explanation += f"\n\n{explanation_text}"
                else:
                    prev_q.explanation = explanation_text
            # Scenario B: It belongs to the last question of the PREVIOUS page (Database)
            else:
                last_db_q = Question.objects.filter(
                    category_id=pdf.category_id
                ).order_by('-id').first()

                if last_db_q:
                    print(f"🔗 Linking orphaned explanation to Question {last_db_q.question_number}")
                    if last_db_q.explanation:
                        last_db_q.explanation += f"\n\n{explanation_text}"
                    else:
                        last_db_q.explanation = explanation_text
                    last_db_q.save()

        # ======================================================
        # --- TYPE 2: FRAGMENT (Continuation from prev page) ---
        # ======================================================
        elif item.get('type') == 'fragment' or item.get('is_continuation'):
            if new_buffer:
                text_part_1 = new_buffer.get('question', '')
                text_part_2 = item.get('question', '')
                full_text = f"{text_part_1} {text_part_2}".strip()

                opts_1 = new_buffer.get('options', [])
                opts_2 = cleaned_options
                full_options = opts_1 + opts_2

                expl_1 = new_buffer.get('explanation', '')
                expl_2 = item.get('explanation', '')
                full_explanation = f"{expl_1} {expl_2}".strip()

                questions_to_create.append(Question(
                    category_id=pdf.category_id,
                    subcategory=new_buffer.get('subcategory') or final_subcategory,
                    question_number=new_buffer.get('question_number'),
                    text=full_text,
                    options=full_options,
                    correct_option=item.get('correct_option') or new_buffer.get('correct_option'),
                    explanation=full_explanation,
                    page_number=page_num
                ))
                new_buffer = None

        # ======================================================
        # --- TYPE 3: NEW QUESTION ---
        # ======================================================
        elif item.get('type') == 'question':
            if item.get('is_incomplete'):
                item['subcategory'] = final_subcategory
                item['options'] = cleaned_options
                new_buffer = item
            else:
                questions_to_create.append(Question(
                    category_id=pdf.category_id,
                    subcategory=final_subcategory,
                    question_number=item.get('question_number'),
                    text=item['question'],
                    options=cleaned_options,
                    correct_option=item.get('correct_option'),
                    explanation=item.get('explanation', ''),
                    page_number=page_num
                ))

    return new_buffer, len(questions_to_create), active_subcat, questions_to_create


def process_next_batch(pdf: PDFUpload, batch_size: int = 10):
    from time import sleep

    buffer = pdf.incomplete_question_data

    # LOAD STATE: Start where we left off (e.g., "Anemiler")
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

        # OPTIMIZATION: In-memory image processing to save Disk I/O
        try:
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=200)
            # Get bytes directly (jpg format)
            img_bytes = pix.tobytes("jpg")
            base64_image = base64.b64encode(img_bytes).decode('utf-8')

            response = groq.get_quiz_content_from_image(base64_image)

            if response:
                response_json = json.loads(response)

                # Pass the state in, get the updated state out
                buffer, count, current_subcat_state, questions_to_create = parse_and_save_questions(
                    pdf,
                    response_json,
                    buffer,
                    current_subcat_state,
                    page_num + 1
                )

                # OPTIMIZATION: Atomic transaction for safety
                with transaction.atomic():
                    if questions_to_create:
                        Question.objects.bulk_create(questions_to_create)
                        total_created += count

                    # SAVE STATE: Save subcategory so next batch (Page 11) knows "We are in Anemiler"
                    pdf.last_processed_page = page_num + 1
                    pdf.incomplete_question_data = buffer
                    pdf.current_subcategory = current_subcat_state
                    pdf.save(update_fields=['last_processed_page', 'incomplete_question_data', 'current_subcategory'])

        except json.JSONDecodeError:
            print(f"Error decoding JSON on page {page_num}")
        except Exception as e:
            print(f"Error processing page {page_num}: {e}")

        close_old_connections()
        sleep(5)

    return f"Processed pages {start_page} to {pdf.last_processed_page}. Added {total_created} questions."


def background_worker(pdf_ids: list[int], batch_size: int) -> None:
    from django.db import connections

    print(f"--- 🚀 Starting Background Batch (Count: {len(pdf_ids)}) ---")
    connections.close_all()

    for pdf_id in pdf_ids:
        try:
            pdf = PDFUpload.objects.get(id=pdf_id)
            print(f"▶️ Processing: {pdf.title}...")

            result = process_next_batch(pdf, batch_size)
            print(f"✅ Finished {pdf.title}: {result}")
        except PDFUpload.DoesNotExist:
            print(f"❌ Error: PDF {pdf_id} not found.")
        except Exception as e:
            print(f"❌ Error processing PDF {pdf_id}: {e}")
        finally:
            try:
                close_old_connections()

                PDFUpload.objects.filter(id=pdf_id).update(is_processing=False)
                print(f"🔓 [BG] Unlocked PDF {pdf_id}")
            except Exception as e:
                print(f"💀 [BG] Critical Error: Could not unlock PDF {pdf_id}: {e}")

            # Cleanup connection
            connections.close_all()

    print("--- 🏁 Batch Complete ---")
