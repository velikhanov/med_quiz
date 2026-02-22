import os
import re
import json
import base64
import subprocess
from typing import Any
from django.conf import settings
import fitz
from time import sleep

from django.db import close_old_connections, transaction, OperationalError

from apps.content.constants import MAX_FILE_SIZE
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



class QuestionParser:
    def __init__(self, pdf: PDFUpload, buffer: dict[str, Any] | None, current_subcat_state: str | None):
        self.pdf = pdf
        self.new_buffer = buffer
        self.active_subcat = current_subcat_state
        self.questions_to_create: list[Question] = []
        self.questions_to_update_map: dict[int, Question] = {}
        self.pending_explanations: dict[int, str] = {}
        self._cached_last_db_q: Question | None = None

    def get_last_db_question(self) -> Question | None:
        if self._cached_last_db_q:
            return self._cached_last_db_q

        q = Question.objects.filter(category_id=self.pdf.category_id).order_by('-id').first()
        if q:
            if q.id in self.questions_to_update_map:
                self._cached_last_db_q = self.questions_to_update_map[q.id]
            else:
                self._cached_last_db_q = q
                self.questions_to_update_map[q.id] = q
        return self._cached_last_db_q

    def clean_options(self, raw_options: list[str]) -> list[str]:
        cleaned_options = []
        if raw_options:
            for idx, opt in enumerate(raw_options):
                opt = opt.strip()
                opt_clean = re.sub(r'^([A-Za-z0-9]+[\.\)\-]\s*)', '', opt)
                cleaned_options.append(f"{chr(65+idx)}) {opt_clean}")
        return cleaned_options

    def handle_box_variant(self, item_text: str, item_explanation: str | None) -> None:
        box_content = item_text
        if item_explanation:
            box_content += f"\n\n{item_explanation}"

        if self.new_buffer:
            self.new_buffer['explanation'] = ((self.new_buffer.get('explanation') or '') + f"\n\n[Alternatif Soru/Kutu]:\n{box_content}").strip()
        elif self.questions_to_create:
            self.questions_to_create[-1].explanation = (self.questions_to_create[-1].explanation or "") + f"\n\n[Alternatif Soru/Kutu]:\n{box_content}"
        else:
            last_db_q = self.get_last_db_question()
            if last_db_q:
                last_db_q.explanation = (last_db_q.explanation or "") + f"\n\n[Alternatif Soru/Kutu]:\n{box_content}"

    def handle_explanation_only(self, item: dict[str, Any]) -> None:
        explanation_text = item.get('explanation') or ''
        linked_q_num = item.get('linked_question_number')

        if linked_q_num:
            # Check in current batch first
            found_in_batch = next((q for q in self.questions_to_create if q.question_number == linked_q_num), None)
            if found_in_batch:
                found_in_batch.explanation = (found_in_batch.explanation or "") + f"\n\n{explanation_text}"
            else:
                # Check in updated map
                found_in_updates = next((q for q in self.questions_to_update_map.values() if q.question_number == linked_q_num), None)
                if found_in_updates:
                    found_in_updates.explanation = (found_in_updates.explanation or "") + f"\n\n{explanation_text}"
                else:
                    # Check in DB
                    try:
                        recent_q = Question.objects.filter(category_id=self.pdf.category_id, question_number=linked_q_num).order_by('-id').first()
                        if recent_q:
                            self.questions_to_update_map[recent_q.id] = recent_q
                            recent_q.explanation = (recent_q.explanation or "") + f"\n\n{explanation_text}"
                        else:
                            # Truly pending or mismatch
                            self.pending_explanations[linked_q_num] = (self.pending_explanations.get(linked_q_num, "") + f"\n\n{explanation_text}").strip()
                    except Exception as e:
                        print(f"Error linking explanation to DB question {linked_q_num}: {e}")
        else:
            if self.new_buffer:
                self.new_buffer['explanation'] = ((self.new_buffer.get('explanation') or '') + f"\n\n{explanation_text}").strip()
            elif self.questions_to_create:
                self.questions_to_create[-1].explanation = (self.questions_to_create[-1].explanation or "") + f"\n\n{explanation_text}"
            else:
                last_db_q = self.get_last_db_question()
                if last_db_q:
                    last_db_q.explanation = (last_db_q.explanation or "") + f"\n\n{explanation_text}"

    def handle_fragment(self, item: dict[str, Any], cleaned_options: list[str], current_item_subcategory: str | None, page_num: int) -> None:
        # It's a continuation if we have a buffer OR if explicitly marked
        if self.new_buffer:
            text_part_1 = self.new_buffer.get('question', '')
            text_part_2 = item.get('question', '')
            full_text = f"{text_part_1} {text_part_2}".strip()

            opts_1 = self.new_buffer.get('options', [])
            opts_2 = cleaned_options
            full_options = opts_1 + opts_2

            expl_1 = self.new_buffer.get('explanation') or ''
            expl_2 = item.get('explanation') or ''

            pending_expl = ""
            q_num = self.new_buffer.get('question_number')
            if q_num and q_num in self.pending_explanations:
                pending_expl = self.pending_explanations.pop(q_num)

            full_explanation = f"{pending_expl}\n{expl_1} {expl_2}".strip()

            # Check if this merged result is STILL incomplete
            if item.get('is_incomplete'):
                self.new_buffer['question'] = full_text
                self.new_buffer['options'] = full_options
                self.new_buffer['explanation'] = full_explanation
                # Keep waiting
            else:
                self.questions_to_create.append(Question(
                    category_id=self.pdf.category_id,
                    subcategory=self.new_buffer.get('subcategory') or current_item_subcategory,
                    question_number=self.new_buffer.get('question_number'),
                    text=full_text,
                    options=full_options,
                    correct_option=get_correct_option(item),
                    explanation=full_explanation,
                    page_number=page_num
                ))
                self.new_buffer = None

    def handle_question(self, item: dict[str, Any], cleaned_options: list[str], current_item_subcategory: str | None, page_num: int) -> None:
        q_num = item.get('question_number')
        pre_filled_explanation = item.get('explanation') or ''

        if q_num and q_num in self.pending_explanations:
            orphan_text = self.pending_explanations.pop(q_num)
            pre_filled_explanation = f"{orphan_text}\n\n{pre_filled_explanation}".strip()

        if item.get('is_incomplete'):
            item['subcategory'] = current_item_subcategory
            item['options'] = cleaned_options
            item['explanation'] = pre_filled_explanation
            self.new_buffer = item
        else:
            self.questions_to_create.append(Question(
                category_id=self.pdf.category_id,
                subcategory=current_item_subcategory,
                question_number=q_num,
                text=item['question'],
                options=cleaned_options,
                correct_option=get_correct_option(item),
                explanation=pre_filled_explanation,
                page_number=page_num
            ))

    def parse(self, response_json: list[dict[str, Any]], page_num: int) -> tuple[dict[str, Any] | None, int, str | None, list[Question], list[Question]]:
        for item in response_json:
            if not item:
                continue

            if item.get('subcategory'):
                self.active_subcat = item['subcategory'].strip().capitalize()
            current_item_subcategory = item.get('subcategory') or self.active_subcat

            cleaned_options = self.clean_options(item.get('options', []))

            item_type = item.get('type')
            item_text = item.get('question', '').strip()
            text_lower = item_text.lower()

            # Define the condition for skipping/merging
            is_box_variant = (
                "şöyle de sorulabilirdi" in text_lower or
                "bu soru" in text_lower or
                (item_type == 'question' and not item.get('options') and not item.get('is_incomplete'))
            )

            if is_box_variant:
                self.handle_box_variant(item_text, item.get('explanation'))
            elif item_type == 'explanation_only':
                self.handle_explanation_only(item)
            elif item_type == 'fragment' or item.get('is_continuation') or (self.new_buffer and not item.get('question_number')):
                self.handle_fragment(item, cleaned_options, current_item_subcategory, page_num)
            elif item_type == 'question':
                self.handle_question(item, cleaned_options, current_item_subcategory, page_num)

        return self.new_buffer, len(self.questions_to_create), self.active_subcat, self.questions_to_create, list(self.questions_to_update_map.values())


def parse_and_save_questions(
    pdf: PDFUpload,
    response_json: list[dict[str, Any]],
    buffer: dict[str, Any] | None,
    current_subcat_state: str | None,
    page_num: int
) -> tuple[dict[str, Any] | None, int, str | None, list[Question], list[Question]]:
    parser = QuestionParser(pdf, buffer, current_subcat_state)
    return parser.parse(response_json, page_num)



def process_next_batch(pdf: PDFUpload, batch_size: int = 10) -> str:
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
            img_bytes = pix.tobytes("png")
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
                                Question.objects.bulk_update(questions_to_update, ['explanation'])

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

    manage_py_path = os.path.join(settings.BASE_DIR, "manage.py")
    id_strs = [str(pid) for pid in pdf_ids]

    command = ["python", manage_py_path, "process_pdf_batch"] + id_strs + ["--batch_size", str(batch_size)]

    with open(log_path, 'a') as log_file:
        subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            cwd=settings.BASE_DIR
        )
