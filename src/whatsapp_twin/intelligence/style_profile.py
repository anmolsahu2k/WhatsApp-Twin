"""StyleProfile dataclass — captures per-contact writing patterns."""

import json
from dataclasses import dataclass, field


@dataclass
class StyleProfile:
    # Message structure
    avg_message_length_chars: float = 0.0
    avg_message_length_words: float = 0.0
    avg_messages_per_turn: float = 1.0
    split_message_ratio: float = 0.0  # fraction of turns with multiple messages

    # Language mixing
    primary_language: str = "en"  # "en", "hi", "hinglish"
    hinglish_ratio: float = 0.0  # fraction of messages with Hindi words
    common_hindi_words: list[str] = field(default_factory=list)

    # Emoji
    emoji_density: float = 0.0  # emojis per message
    top_emojis: list[str] = field(default_factory=list)
    laughing_style: str = ""  # "haha", "lol", "😂", etc.

    # Punctuation/casing
    period_usage_ratio: float = 0.0  # fraction of messages ending with "."
    capitalization_style: str = "lowercase"  # "lowercase", "sentence", "mixed"
    ellipsis_frequency: float = 0.0

    # Abbreviations/slang
    common_abbreviations: dict[str, str] = field(default_factory=dict)
    filler_words: list[str] = field(default_factory=list)
    greeting_patterns: list[str] = field(default_factory=list)
    farewell_patterns: list[str] = field(default_factory=list)

    # Sentence rhythm
    avg_words_per_sentence: float = 0.0
    common_connectors: list[str] = field(default_factory=list)

    # Qualitative (from LLM analysis)
    qualitative_summary: str = ""
    tone_description: str = ""

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "StyleProfile":
        d = json.loads(data)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_prompt_description(self) -> str:
        """Generate a concise style description for the LLM prompt."""
        parts = []

        parts.append(f"Average message length: {self.avg_message_length_words:.0f} words")
        if self.avg_messages_per_turn > 1.2:
            parts.append(f"Often splits into {self.avg_messages_per_turn:.1f} messages per turn")

        if self.hinglish_ratio > 0.1:
            parts.append(f"Mixes Hindi ({self.hinglish_ratio:.0%} of messages)")
            if self.common_hindi_words:
                parts.append(f"Common Hindi words: {', '.join(self.common_hindi_words[:10])}")

        if self.emoji_density > 0:
            parts.append(f"Emoji density: {self.emoji_density:.2f} per message")
            if self.top_emojis:
                parts.append(f"Favorite emojis: {' '.join(self.top_emojis[:5])}")

        if self.laughing_style:
            parts.append(f"Laughing style: {self.laughing_style}")

        parts.append(f"Capitalization: {self.capitalization_style}")

        if self.period_usage_ratio < 0.1:
            parts.append("Rarely uses periods")
        elif self.period_usage_ratio > 0.5:
            parts.append("Often ends with periods")

        if self.common_abbreviations:
            abbrevs = [f"{k}→{v}" for k, v in list(self.common_abbreviations.items())[:5]]
            parts.append(f"Abbreviations: {', '.join(abbrevs)}")

        if self.filler_words:
            parts.append(f"Filler words: {', '.join(self.filler_words[:5])}")

        if self.qualitative_summary:
            parts.append(f"Overall style: {self.qualitative_summary}")

        return "\n".join(f"- {p}" for p in parts)
