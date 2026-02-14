MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"

QUIZ_PROMPT = """
You are a medical exam transcriptionist processing a page from a Turkish medical textbook.
The page has a two-column layout.

### CRITICAL READING STRATEGY
1. Read the **Left Column** (Top to Bottom).
2. Then read the **Right Column** (Top to Bottom).
3. **Subcategory Scope:** A header title (e.g., "HEMATOPOEZ") ONLY applies to questions **below it in the SAME column**. Do not apply a Right Column header to a Left Column question.

### PART 1: EXTRACTION RULES
- `question_number`: Integer.
- `question`: Full text.
- `options`: List of strings. Just extract the text.
- `correct_option`: Letter only.
- `subcategory`: The bold header explicitly above this specific question.
- `explanation`: Text.
  * Capture the paragraph immediately following the options.
  * **FORMATTING:** Preserve lists/newlines.

### PART 2: ORPHANED / DISPLACED EXPLANATIONS
Sometimes an explanation appears in the Left Column but belongs to a question in the Right Column.
If you find a paragraph ending with "Doğru cevap: [X]" that is NOT immediately after a question:
  1. Set "type": "explanation_only".
  2. Extract the text.
  3. **CRITICAL:** Look at the page. Which question number does this text belong to? (e.g., if it talks about "Kompanzasyon" and Question 2 asks about that, link it).
  4. Add field: `"linked_question_number": <integer>`.

### OUTPUT FORMAT (JSON List)
[
  {
    "type": "question",
    "question_number": 1,
    "subcategory": "HEMATOPOEZ",
    "question": "...",
    "options": [...],
    "correct_option": "C"
  },
  {
    "type": "explanation_only",
    "linked_question_number": 2,
    "explanation": "Dokulara oksijen sunumunu... Doğru cevap: A"
  },
  {
    "type": "question",
    "question_number": 2,
    "subcategory": "ANEMİLER",
    "question": "...",
    "options": [...],
    "correct_option": "A"
  }
]
"""

TEMPERATURE = 0.1
MAX_TOKENS = 4096
