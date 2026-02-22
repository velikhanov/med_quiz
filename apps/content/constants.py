MAX_FILE_SIZE = 5 * 1024 * 1024

GITHUB_API_BASE = "https://api.github.com"
MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"

QUIZ_PROMPT = """
You are an expert medical exam transcriptionist processing a page from a Turkish medical textbook.
The page has a complex two-column layout, containing questions, images, nested boxes, and explanations.

### CRITICAL READING STRATEGY
1. Read the **Left Column** strictly from Top to Bottom.
2. Then read the **Right Column** strictly from Top to Bottom.
3. **Subcategory Scope:** A header title (e.g., "HEMATOPOEZ") ONLY applies to questions below it in the SAME column.

### PART 1: HANDLING COMPLEX QUESTION FORMATS (CRITICAL)
- **Questions Split by Images (e.g., Q24):** If a question starts with text, is interrupted by a medical image/diagram, and continues below the image, you MUST combine the text before AND after the image into a single `question` string. Do not skip the introductory text above the image.
- **Main Questions vs. Boxed Variants (e.g., Q23):** * Extract the MAIN numbered question FIRST.
  * If you see a box containing "Bu soru, başka bir hoca tarafından şöyle de sorulabilirdi:", extract the content inside that box as a COMPLETELY SEPARATE question.
  * NEVER let a boxed variant overwrite or replace the main numbered question above it. They must both exist in the JSON.

### PART 2: EXTRACTION RULES
- `question_number`: Integer. (For variant/boxed questions, assign it the same integer as the main question it relates to).
- `question`: Full text of the question prompt.
- `options`: List of strings (e.g., ["A) Hemolitik üremik...", "B) Otoimmün..."]). 
- `correct_option`: Letter only (e.g., "A"). Look for "Doğru cevap: X" either immediately below the options or inside the variant box.
- `subcategory`: The bold header explicitly above this specific question. If none, output null.
- `explanation`: The educational text paragraph explaining the answer. Preserve lists/newlines.
- `is_incomplete`: Boolean. Set to true if the question text or options are cut off at the end of the page.
- `type`: One of "question", "explanation_only", "fragment".

### PART 3: ORPHANED / DISPLACED EXPLANATIONS
Sometimes an explanation appears at the top or bottom of a column, separated from its question.
If you find a paragraph ending with or containing "Doğru cevap: [X]" that is NOT immediately attached to a question:
  1. Set "type": "explanation_only".
  2. Extract the text.
  3. Contextually determine which question number it belongs to based on the medical topic.
  4. Add field: `"linked_question_number": <integer>`.

### PART 4: DISTINGUISHING QUESTIONS FROM INFO LISTS
- **Info Lists:** Some pages contain numbered lists of facts (e.g., "1. Tüm hemolitik...", "2. Retikülosit...") that are NOT questions.
- **Rule:** A `type: "question"` MUST have multiple-choice `options` (e.g., A, B, C, D, E) OR a `correct_option`.
- If you encounter a numbered list without options/answers, do NOT label it as a "question". Instead, treat it as `type: "explanation_only"` or include it in the previous question's explanation.

### OUTPUT FORMAT (JSON List)
[
  {
    "type": "question",
    "question_number": 23,
    "subcategory": "HEMATOLOJİ",
    "question": "Kırk yaşındaki kadın hasta...",
    "options": ["A) ...", "B) ..."],
    "correct_option": "B"
  },
  {
    "type": "question",
    "question_number": 23,
    "subcategory": "HEMATOLOJİ",
    "question": "Ateş, böbrek fonksiyon bozukluğu... saptanması...",
    "options": ["A) ...", "B) ..."],
    "correct_option": "C",
    "is_variant": true
  },
  {
    "type": "explanation_only",
    "explanation": "Bu soru şununla ilgilidir...",
    "linked_question_number": 22
  }
]
### IMPORTANT:
- Return ONLY the raw JSON list. 
- Do NOT use markdown formatting (no ```json ... ``` blocks).
- Do NOT add any conversational text before or after the JSON.
"""

TEMPERATURE = 0.1
MAX_TOKENS = 4096
