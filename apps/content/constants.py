MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"

QUIZ_PROMPT = """
You are a medical exam transcriptionist processing a page from a Turkish medical textbook.
The page has a two-column layout. Read the columns top-to-bottom, left-to-right.

### PART 1: HIERARCHY DETECTION
1. **Main Category:** Look for a running header (e.g., "HEMATOLOJİ"). If not found, null.
2. **Subcategory:** Look for bold, centered section titles (e.g., "ANEMİLER"). Apply the most recent one found.

### PART 2: SPLIT QUESTION LOGIC
1. **Start of Page (Continuation):** If the page starts with options (e.g., "C)...") or the end of a sentence, set "type": "fragment" and "is_continuation": true.
2. **End of Page (Incomplete):** If the last question is cut off, mark it with "is_incomplete": true.

### PART 3: EXTRACTION RULES
- `question_number`: Integer (e.g., 42).
- `question`: Full text (exclude the number).
- `options`: List of strings.
- `correct_option`: Letter only (e.g. "A").
- `explanation`: Text. **CRITICAL:** If it contains a Table, TRANSCRIBE IT to Markdown. Do NOT flag as image.

### OUTPUT FORMAT (JSON List)
[
  { "type": "fragment", "is_continuation": true, "options": ["C)..."], "correct_option": "C", "explanation": "..." },
  { "type": "question", "question_number": 42, "question": "...", "options": ["A)..."], "correct_option": "A", "explanation": "...", "is_incomplete": false, "subcategory": "ANEMİLER" }
]
"""

TEMPERATURE = 0.1
MAX_TOKENS = 4096
