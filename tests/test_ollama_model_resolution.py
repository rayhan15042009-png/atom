"""Chat must not stall on an Ollama model that was never pulled.

The configured ``model_chat`` used to be sent to ``/api/chat`` unconditionally.
When that name is not installed, Ollama either answers 404 or sits on the load
until the 60 s timeout, while the UI shows "Atom is thinking…" with no answer.
The configured model is now checked against ``/api/tags`` first and the largest
installed chat model is used instead (the substitution is logged).
"""

from __future__ import annotations

import unittest
from unittest import mock

from core.providers import ollama


def _model(name: str, params: str = "8.0B", size: int = 4_000_000_000) -> dict:
    return {"name": name, "size": size, "details": {"parameter_size": params}}


class TestResolveChatModel(unittest.TestCase):
    def setUp(self) -> None:
        ollama._MODELS_CACHE.clear()
        self.addCleanup(ollama._MODELS_CACHE.clear)

    def _resolve(self, configured: str, models: list[dict]) -> tuple[str, str | None]:
        with mock.patch.object(ollama, "installed_models", lambda *a, **k: models):
            return ollama.resolve_chat_model(configured, url="http://x")

    def test_installed_model_is_used_as_configured(self) -> None:
        model, note = self._resolve("llama3.1:8b", [_model("llama3.1:8b"), _model("phi3:mini")])
        self.assertEqual(model, "llama3.1:8b")
        self.assertIsNone(note)

    def test_missing_model_falls_back_to_the_largest_installed(self) -> None:
        model, note = self._resolve("phi3:mini", [
            _model("llama3.1:8b", "8.0B"),
            _model("gemma2:2b", "2.0B"),
            _model("nomic-embed-text:latest", "137M"),
        ])
        self.assertEqual(model, "llama3.1:8b")
        self.assertIn("phi3:mini", note or "")
        self.assertIn("not installed", note or "")

    def test_untagged_name_matches_latest(self) -> None:
        model, note = self._resolve("llama3.1", [_model("llama3.1:latest")])
        self.assertEqual(model, "llama3.1:latest")
        self.assertIsNone(note)

    def test_embedding_models_are_never_chosen(self) -> None:
        model, note = self._resolve("missing:7b", [_model("nomic-embed-text:latest", "137M")])
        self.assertEqual(model, "missing:7b", "a missing model must not become an embedder")
        self.assertIsNone(note)

    def test_nothing_known_leaves_the_request_alone(self) -> None:
        """Server unreachable → no model list → the configured name is kept."""
        model, note = self._resolve("phi3:mini", [])
        self.assertEqual(model, "phi3:mini")
        self.assertIsNone(note)

    def test_an_unreachable_server_is_remembered_too(self) -> None:
        """Ollama down: caching the empty answer stops every request paying it."""
        calls: list[str] = []
        with mock.patch.object(
            ollama, "list_models", lambda url, timeout: calls.append(url) or []
        ):
            ollama._MODELS_CACHE.clear()
            self.assertEqual(ollama.installed_models("http://x"), [])
            self.assertEqual(ollama.installed_models("http://x"), [])
        self.assertEqual(len(calls), 1, "a dead Ollama was probed twice")

    def test_installed_models_are_cached(self) -> None:
        calls: list[str] = []
        with mock.patch.object(
            ollama, "list_models", lambda url, timeout: calls.append(url) or [_model("a:1b")]
        ):
            ollama._MODELS_CACHE.clear()
            ollama.installed_models("http://x")
            ollama.installed_models("http://x")
        self.assertEqual(len(calls), 1, "/api/tags was queried for every request")


class TestRouterUsesTheResolver(unittest.TestCase):
    def test_chat_path_resolves_the_model(self) -> None:
        import inspect

        from core.providers.router import ProviderRouter

        source = inspect.getsource(ProviderRouter._try_ollama)
        self.assertIn("resolve_chat_model", source)


if __name__ == "__main__":
    unittest.main()
