from __future__ import annotations

import re
import threading
from dataclasses import dataclass


@dataclass
class PrivacyRule:
    id: int
    scope: str
    match_mode: str
    pattern: str
    enabled: bool
    updated_ts: int


@dataclass
class _CompiledRule:
    rule: PrivacyRule
    normalized_pattern: str
    regex: re.Pattern[str] | None


class PrivacyFilter:
    def __init__(self, rules: list[PrivacyRule] | None = None) -> None:
        self._lock = threading.Lock()
        self._compiled_rules: list[_CompiledRule] = []
        self.update_rules(rules or [])

    def update_rules(self, rules: list[PrivacyRule]) -> None:
        compiled: list[_CompiledRule] = []
        for rule in rules:
            if not rule.enabled:
                continue
            pattern = (rule.pattern or "").strip()
            if not pattern:
                continue

            regex_obj: re.Pattern[str] | None = None
            if rule.match_mode == "regex":
                try:
                    regex_obj = re.compile(pattern, flags=re.IGNORECASE)
                except re.error:
                    # Regex inválida: la ignoramos para no romper el tracker.
                    continue

            compiled.append(
                _CompiledRule(
                    rule=rule,
                    normalized_pattern=pattern.casefold(),
                    regex=regex_obj,
                )
            )

        with self._lock:
            self._compiled_rules = compiled

    def match_reason(self, app: str, title: str) -> PrivacyRule | None:
        app_text = (app or "").strip()
        title_text = (title or "").strip()
        app_case = app_text.casefold()
        title_case = title_text.casefold()

        with self._lock:
            compiled_rules = list(self._compiled_rules)

        for item in compiled_rules:
            rule = item.rule
            value = title_text if rule.scope == "title" else app_text
            value_case = title_case if rule.scope == "title" else app_case
            if not value:
                continue

            if rule.match_mode == "contains":
                if item.normalized_pattern in value_case:
                    return rule
                continue

            if rule.match_mode == "exact":
                if value_case == item.normalized_pattern:
                    return rule
                continue

            if rule.match_mode == "regex" and item.regex is not None:
                if item.regex.search(value):
                    return rule

        return None

    def is_excluded(self, app: str, title: str) -> bool:
        return self.match_reason(app=app, title=title) is not None

    def stats(self) -> dict[str, int]:
        with self._lock:
            rules = list(self._compiled_rules)

        by_scope = {
            "app": 0,
            "title": 0,
        }
        for item in rules:
            by_scope[item.rule.scope] = by_scope.get(item.rule.scope, 0) + 1

        return {
            "enabled_rules": len(rules),
            "app_rules": by_scope.get("app", 0),
            "title_rules": by_scope.get("title", 0),
        }
