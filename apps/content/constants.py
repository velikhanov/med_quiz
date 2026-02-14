MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"

QUIZ_PROMPT = """
You are a medical exam transcriptionist processing a page from a Turkish medical textbook.
The page has a two-column layout. Read the columns top-to-bottom, left-to-right.

### PART 1: HIERARCHY DETECTION
1. **Main Category:** Look for a running header. If not found, null.
2. **Subcategory:** Look for bold, centered section titles. Apply the most recent one found.

### PART 2: FILTERING RULES
1. **Global Summaries:** Ignore large sidebars titled "Önemli Bilgiler" or "Özet".
2. **Inline Q&A:** Ignore one-line "Question... Answer" formats.
3. **Keep Explanations:** Do NOT ignore text immediately following a question.

### PART 3: EXTRACTION RULES
- `question_number`: Integer.
- `question`: Full text.
- `options`: List of strings. Just extract the text.
- `correct_option`: Letter only.
- `explanation`: Text. 
  * Capture the paragraph immediately following the options.
  * **FORMATTING (CRITICAL):** Preserve the visual structure.
  * If there are lists, bullet points, or checkmarks (✓), **keep them on separate lines**.
  * Do not merge list items into a single paragraph.

### PART 4: SPLIT LOGIC & ORPHANS
1. **Continuation:** If the page starts with options, set "type": "fragment", "is_continuation": true.
2. **Incomplete:** If the last question is cut off, set "is_incomplete": true.
3. **Orphaned Explanation:** If the top of the column/page contains a paragraph that explains a medical concept and ends with "Doğru cevap: [X]", but has NO question text above it:
  - Set "type": "explanation_only".
  - Extract the text into "explanation".
  - **FORMATTING:** Preserve all newlines and symbols (like ✓, -, •). Do not flatten the text.

### OUTPUT FORMAT (JSON List)
[
  { "type": "explanation_only", "explanation": "Kompanzasyon mekanizmaları:\n✓ Kardiyak output artar.\n✓ Kalp atım sayısı artar.\nDoğru cevap: A" },
  { "type": "question", "question_number": 2, "question": "...", "options": [...], "correct_option": "A", "explanation": "..." }
]
"""

TEMPERATURE = 0.1
MAX_TOKENS = 4096
