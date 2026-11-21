"""Analysis must always run locally, on the biggest model Ollama actually has."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.providers.ollama import pick_largest_model
from core.providers.router import ProviderRouter


def _model(name: str, params: str, size: int = 0) -> dict:
    return {"name": name, "size": size, "details": {"parameter_size": params}}


class TestPickLargestModel(unittest.TestCase):
    def test_picks_the_biggest_parameter_count(self) -> None:
        models = [_model("phi3:mini", "3.8B"), _model("dolphin-mistral:7b", "7.2B"),
                  _model("llama3:8b", "8.0B")]
        self.assertEqual(pick_largest_model(models), "llama3:8b")

    def test_ignores_embedding_models(self) -> None:
        models = [_model("nomic-embed-text", "137M"), _model("phi3:mini", "3.8B")]
        self.assertEqual(pick_largest_model(models), "phi3:mini")

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(pick_largest_model([]))


class TestAnalysisRouting(unittest.TestCase):
    def _router(self, provider: str) -> ProviderRouter:
        return ProviderRouter({"llm_provider": provider, "model_analysis": "configured:7b"})

    def test_analysis_never_uses_gemini(self) -> None:
        router = self._router("gemini")
        with patch("core.providers.router.call_gemini") as gemini, \
                patch("core.providers.router.call_ollama", return_value="local answer"):
            out = router.analyze([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "local answer")
        gemini.assert_not_called()

    def test_cloud_chat_uses_the_biggest_installed_model(self) -> None:
        router = self._router("gemini")
        with patch("core.providers.router.largest_installed_model", return_value="llama3:70b"), \
                patch("core.providers.router.call_ollama", return_value="ok") as ollama:
            router.analyze([{"role": "user", "content": "hi"}])
        self.assertEqual(ollama.call_args.kwargs["model"], "llama3:70b")

    def test_ollama_chat_keeps_the_configured_analysis_model(self) -> None:
        router = self._router("ollama")
        with patch("core.providers.router.call_ollama", return_value="ok") as ollama:
            router.analyze([{"role": "user", "content": "hi"}])
        self.assertEqual(ollama.call_args.kwargs["model"], "configured:7b")

    def test_analysis_failure_returns_none(self) -> None:
        router = self._router("gemini")
        with patch("core.providers.router.call_ollama", return_value=None):
            self.assertIsNone(router.analyze([{"role": "user", "content": "hi"}]))


if __name__ == "__main__":
    unittest.main()
