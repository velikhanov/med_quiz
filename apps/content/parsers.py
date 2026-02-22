import re
from typing import Any
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
    def __init__(self, pdf: PDFUpload, buffer: dict[str, Any] | None, current_subcat_state: str | None) -> None:
        self.pdf = pdf
        self.new_buffer = buffer
        self.active_subcat = current_subcat_state
        self.questions_to_create: list[Question] = []
        self.questions_to_update_map: dict[int, Question] = {}
        self.pending_explanations: dict[int, str] = {}
        self._cached_last_db_q: Question | None = None
        self.page_num: int = 0

    def get_last_db_question(self) -> Question | None:
        if self._cached_last_db_q:
            return self._cached_last_db_q

        qs = Question.objects.filter(category_id=self.pdf.category_id)
        q = qs.order_by('-id').first()

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
        print(f"📦 Processing Box Variant: {item_text[:30]}...")
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
                    # Check in DB with Safety Filters
                    try:
                        qs = Question.objects.filter(
                            category_id=self.pdf.category_id, 
                            question_number=linked_q_num
                        )

                        recent_q = None

                        # Filter 2: Subcategory Preference
                        if self.active_subcat:
                            recent_q = qs.filter(subcategory=self.active_subcat).order_by('-id').first()

                        # Fallback: If no subcategory match (or no subcat), take the most recent in range
                        if not recent_q:
                            recent_q = qs.order_by('-id').first()

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
        print(f"🧩 Processing Fragment on Page {page_num}")
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
                print(f"✅ Creating Question from Fragment (Page {page_num})")
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
        else:
            # Fallback: Treat as continuation of last question (implicit buffer via DB/List)
            target_q = None
            if self.questions_to_create:
                target_q = self.questions_to_create[-1]
            else:
                target_q = self.get_last_db_question()

            if target_q:
                print(f"🔗 Appending Fragment to Question {target_q.question_number} (ID: {getattr(target_q, 'id', 'New')})")
                text_part = (item.get('question') or '').strip()
                expl_part = (item.get('explanation') or '').strip()

                if text_part:
                    target_q.text = (target_q.text + " " + text_part).strip()

                if cleaned_options:
                    if target_q.options is None:
                        target_q.options = []
                    if isinstance(target_q.options, list):
                        target_q.options.extend(cleaned_options)

                if expl_part:
                    target_q.explanation = (target_q.explanation or "") + "\n" + expl_part

                # If we modified a DB question, ensure it's in the update map
                if target_q not in self.questions_to_create and hasattr(target_q, 'id'):
                    self.questions_to_update_map[target_q.id] = target_q

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
            print(f"🔄 Buffering Incomplete Question {q_num} (Page {page_num})")
        else:
            print(f"✅ Creating Question {q_num} (Page {page_num})")
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
        self.page_num = page_num
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
            # Treat as box variant ONLY if it lacks a question number OR explicitly has box keywords
            is_box_variant = (
                "şöyle de sorulabilirdi" in text_lower or
                "bu soru" in text_lower or
                (
                    item_type == 'question' 
                    and not item.get('options') 
                    and not item.get('is_incomplete')
                    and not item.get('question_number')  # Safety: If it has a number, it's a real question
                )
            )

            # Determine if this item is a fragment/continuation
            item_q_num = item.get('question_number')
            is_fragment = False

            # Case 1: Explicit fragment/continuation without a new number
            if (item_type == 'fragment' or item.get('is_continuation')) and not item_q_num:
                is_fragment = True
            # Case 2: Implicit continuation (buffer exists and no new number to interrupt it)
            elif self.new_buffer and not item_q_num:
                is_fragment = True
            # Case 3: Explicit continuation matching the buffer's number
            elif self.new_buffer and item_q_num and item_q_num == self.new_buffer.get('question_number'):
                is_fragment = True

            if is_box_variant:
                print(f"📦 Merging Box Variant (Page {page_num}): {item_text[:50]}...")
                self.handle_box_variant(item_text, item.get('explanation'))
            elif item_type == 'explanation_only':
                self.handle_explanation_only(item)
            elif is_fragment:
                self.handle_fragment(item, cleaned_options, current_item_subcategory, page_num)
            elif item_type == 'question' or item_q_num:
                # If it has a question number, force it to be treated as a question (even if type was 'fragment' but mismatched buffer)
                self.handle_question(item, cleaned_options, current_item_subcategory, page_num)
            else:
                print(f"⚠️ Unhandled Item Type '{item_type}' on Page {page_num}: {item_text[:50]}...")

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
