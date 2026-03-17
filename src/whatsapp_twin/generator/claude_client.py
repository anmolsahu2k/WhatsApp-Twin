"""Anthropic Claude API wrapper with streaming support."""

from collections.abc import Generator

from anthropic import Anthropic

from whatsapp_twin.config.settings import Settings


class ClaudeClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        api_key = settings.get_api_key()
        if not api_key:
            raise ValueError("No ANTHROPIC_API_KEY found in environment or .env file")
        self._client = Anthropic(api_key=api_key)

    def generate(self, system: str, user_message: str,
                 model: str | None = None,
                 max_tokens: int | None = None) -> str:
        """Generate a response from Claude (non-streaming).

        Args:
            system: System prompt.
            user_message: User message content.
            model: Override model (defaults to settings).
            max_tokens: Override max tokens (defaults to settings).

        Returns:
            Generated text response.
        """
        response = self._client.messages.create(
            model=model or self.settings.anthropic_model,
            max_tokens=max_tokens or self.settings.anthropic_max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    def generate_stream(self, system: str, user_message: str,
                        model: str | None = None,
                        max_tokens: int | None = None) -> Generator[str, None, None]:
        """Generate a response from Claude with streaming.

        Yields text chunks as they arrive. Use for lower perceived latency.

        Args:
            system: System prompt.
            user_message: User message content.
            model: Override model (defaults to settings).
            max_tokens: Override max tokens (defaults to settings).

        Yields:
            Text chunks as they are generated.
        """
        with self._client.messages.stream(
            model=model or self.settings.anthropic_model,
            max_tokens=max_tokens or self.settings.anthropic_max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                yield text

    def analyze_style_qualitative(self, messages_sample: list[str],
                                   contact_name: str) -> dict:
        """Run qualitative style analysis on a sample of messages.

        Sends message samples to Claude for tone/rhythm/tic analysis.

        Args:
            messages_sample: List of 30-50 representative messages.
            contact_name: Name of the contact (for context).

        Returns:
            Dict with qualitative style attributes.
        """
        sample_text = "\n".join(f"- {m}" for m in messages_sample[:50])

        system = (
            "You are a linguistics analyst. Analyze the writing style of these WhatsApp messages "
            "from one person. Return a JSON object with these fields:\n"
            "- tone: one-sentence description of overall tone\n"
            "- rhythm: how sentences flow (short/choppy, flowing, mixed)\n"
            "- quirks: list of unique verbal tics or patterns\n"
            "- formality: scale 1-5 (1=very casual, 5=very formal)\n"
            "- humor_style: description of humor if present\n"
            "- summary: 2-3 sentence overall style description\n\n"
            "Return ONLY valid JSON, no markdown fencing."
        )

        user_msg = (
            f"Analyze the writing style of messages sent to {contact_name}:\n\n"
            f"{sample_text}"
        )

        response = self.generate(system, user_msg)

        import json
        try:
            # Strip any markdown fencing if present
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"summary": response, "tone": "", "rhythm": "", "quirks": [],
                    "formality": 3, "humor_style": ""}
