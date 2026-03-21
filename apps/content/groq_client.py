import os

from groq import Groq

from apps.content.constants import MODEL_NAME, QUIZ_PROMPT, TEMPERATURE, MAX_TOKENS


class GroqClient:
    def __init__(self):
        self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

    def get_quiz_content_from_image(self, base64_image: str, context_text: str | None = None) -> str | None:
        messages = []

        if context_text:
            messages.append({
                "role": "system",
                "content": f"PREVIOUS PAGE CONTEXT:\n{context_text}\n\nUse this context to handle fragments or missing data at the top of the current page."
            })

        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": QUIZ_PROMPT
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    }
                }
            ]
        })

        completion = self.client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS
        )

        return completion.choices[0].message.content if completion.choices else None
