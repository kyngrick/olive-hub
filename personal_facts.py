"""Simple JSON-based personal facts memory (no embeddings, fast)."""
from __future__ import annotations
import json, os, re, time
from typing import Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FACTS_FILE = os.environ.get("PERSONAL_FACTS_FILE") or os.path.join(BASE_DIR, "personal_facts.json")

class PersonalFacts:
    def __init__(self):
        self.facts: dict[str, dict] = {}
        self._load()

    def _load(self):
        if os.path.isfile(FACTS_FILE):
            try:
                with open(FACTS_FILE, encoding="utf-8-sig") as h:
                    self.facts = json.load(h)
            except Exception:
                self.facts = {}

    def _save(self):
        try:
            with open(FACTS_FILE, "w", encoding="utf-8") as h:
                json.dump(self.facts, h, indent=2)
        except Exception:
            pass

    def remember(self, key: str, value: str) -> None:
        self.facts[key.lower().strip()] = {
            "value": value.strip(),
            "saved_at": time.time(),
        }
        self._save()

    @staticmethod
    def _value(entry: Any) -> str:
        """Accept both the current {"value": ...} shape and bare strings."""
        if isinstance(entry, dict):
            return str(entry.get("value") or "")
        return str(entry or "")

    def recall(self, query: str) -> Optional[str]:
        q = query.lower().strip()
        if not q:
            return None
        # Direct match
        if q in self.facts:
            return self._value(self.facts[q])
        # Substring match
        for key, entry in self.facts.items():
            if q in key or key in q:
                return self._value(entry)
        return None

    def extract_and_store(self, text: str) -> Optional[str]:
        """Detect 'my X is Y' or 'I like X' patterns and store."""
        # "my birthday is March 5"
        m = re.search(r"\bmy\s+(\w+(?:\s+\w+)?)\s+(?:is|are)\s+(.+)", text, re.IGNORECASE)
        if m:
            key, value = m.group(1).strip(), m.group(2).strip(" .")
            self.remember(key, value)
            return f"I'll remember that your {key} is {value}."

        # "I like X" / "I love X"
        m = re.search(r"\bI\s+(?:like|love|prefer)\s+(.+)", text, re.IGNORECASE)
        if m:
            thing = m.group(1).strip(" .")
            self.remember(f"likes_{thing.lower()}", thing)
            return f"I'll remember that you like {thing}."

        return None