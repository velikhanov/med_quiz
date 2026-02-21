import re
import json
import base64
from typing import Any
import fitz
from time import sleep

from django.db import close_old_connections, transaction, connections

from apps.content.groq_client import GroqClient
from apps.content.models import PDFUpload, Question


def get_correct_option(item: dict[str, Any]) -> str:
    correct_opt = item.get('correct_option')
    if not correct_opt:
        correct_opt = '?'
    elif len(correct_opt) > 1:
        # Fixes if AI accidentally returns "A)" instead of "A"
        correct_opt = correct_opt[0].upper()

    return correct_opt


def parse_and_save_questions(pdf, response_json, buffer, current_subcat_state, page_num):
    questions_to_create = []
    new_buffer = buffer
    active_subcat = current_subcat_state
    pending_explanations = {}

    for item in response_json:
        if not item:
            continue

        if item.get('subcategory'):
            active_subcat = item['subcategory'].strip().capitalize()
        current_item_subcategory = item.get('subcategory') or active_subcat

        raw_options = item.get('options', [])
        cleaned_options = []
        if raw_options:
            for idx, opt in enumerate(raw_options):
                opt = str(opt).strip()
                opt_clean = re.sub(r'^([A-Za-z0-9]+[\.\)\-]\s*)', '', opt)
                cleaned_options.append(f"{chr(65+idx)}) {opt_clean}")

        item_type = item.get('type')

        if item_type == 'explanation_only':
            explanation_text = item.get('explanation', '')
            linked_q_num = item.get('linked_question_number')

            if linked_q_num:
                found_in_batch = next((q for q in questions_to_create if q.question_number == linked_q_num), None)
                if found_in_batch:
                    found_in_batch.explanation = (found_in_batch.explanation or "") + f"\n\n{explanation_text}"
                else:
                    pending_explanations[linked_q_num] = (pending_explanations.get(linked_q_num, "") + f"\n\n{explanation_text}").strip()
            else:
                if new_buffer:
                    new_buffer['explanation'] = (new_buffer.get('explanation', '') + f"\n\n{explanation_text}").strip()
                elif questions_to_create:
                    questions_to_create[-1].explanation += f"\n\n{explanation_text}"
                else:
                    last_db_q = Question.objects.filter(category_id=pdf.category_id).order_by('-id').first()
                    if last_db_q:
                        last_db_q.explanation = (last_db_q.explanation or "") + f"\n\n{explanation_text}"
                        last_db_q.save(update_fields=['explanation'])

        elif item_type == 'fragment' or item.get('is_continuation'):
            if new_buffer:
                text_part_1 = new_buffer.get('question', '')
                text_part_2 = item.get('question', '')
                full_text = f"{text_part_1} {text_part_2}".strip()

                opts_1 = new_buffer.get('options', [])
                opts_2 = cleaned_options
                full_options = opts_1 + opts_2

                expl_1 = new_buffer.get('explanation', '')
                expl_2 = item.get('explanation', '')

                pending_expl = ""
                q_num = new_buffer.get('question_number')
                if q_num and q_num in pending_explanations:
                    pending_expl = pending_explanations.pop(q_num)

                full_explanation = f"{pending_expl}\n{expl_1} {expl_2}".strip()

                questions_to_create.append(Question(
                    category_id=pdf.category_id,
                    subcategory=new_buffer.get('subcategory') or current_item_subcategory,
                    question_number=new_buffer.get('question_number'),
                    text=full_text,
                    options=full_options,
                    correct_option=get_correct_option(item),
                    explanation=full_explanation,
                    page_number=page_num
                ))
                new_buffer = None

        elif item_type == 'question':
            q_num = item.get('question_number')
            pre_filled_explanation = item.get('explanation', '')

            if q_num and q_num in pending_explanations:
                orphan_text = pending_explanations.pop(q_num)
                pre_filled_explanation = f"{orphan_text}\n\n{pre_filled_explanation}".strip()

            if item.get('is_incomplete'):
                item['subcategory'] = current_item_subcategory
                item['options'] = cleaned_options
                item['explanation'] = pre_filled_explanation
                new_buffer = item
            else:
                questions_to_create.append(Question(
                    category_id=pdf.category_id,
                    subcategory=current_item_subcategory,
                    question_number=q_num,
                    text=item['question'],
                    options=cleaned_options,
                    correct_option=get_correct_option(item),
                    explanation=pre_filled_explanation,
                    page_number=page_num
                ))

    return new_buffer, len(questions_to_create), active_subcat, questions_to_create


def process_next_batch(pdf: PDFUpload, batch_size: int = 10):
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
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("jpg")
            base64_image = base64.b64encode(img_bytes).decode('utf-8')

            response = groq.get_quiz_content_from_image(base64_image)

            if response:
                response_json = json.loads(response)

                buffer, count, current_subcat_state, questions_to_create = parse_and_save_questions(
                    pdf,
                    response_json,
                    buffer,
                    current_subcat_state,
                    page_num + 1
                )

                try:
                    with transaction.atomic():
                        if questions_to_create:
                            Question.objects.bulk_create(questions_to_create)
                            total_created += count
                except Exception as e:
                    print(f"❌ Database Error saving questions on Page {page_num}: {e}")
                    print(f"⏭️ Skipping Page {page_num} to prevent infinite loop.")

                pdf.last_processed_page = page_num + 1
                pdf.incomplete_question_data = buffer
                pdf.current_subcategory = current_subcat_state
                pdf.save(update_fields=['last_processed_page', 'incomplete_question_data', 'current_subcategory'])

        except json.JSONDecodeError:
            print(f"Error decoding JSON on page {page_num}")
        except Exception as e:
            print(f"Error processing page {page_num}: {e}")

        close_old_connections()
        sleep(1)

    return f"Processed pages {start_page} to {pdf.last_processed_page}. Added {total_created} questions."


def background_worker(pdf_ids: list[int], batch_size: int) -> None:
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

            connections.close_all()

    print("--- 🏁 Batch Complete ---")
