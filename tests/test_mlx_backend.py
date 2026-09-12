import unittest
from unittest.mock import patch

from local_lean_agent.backends.mlx import MLXBackend
from local_lean_agent.config import MLXConfig
from local_lean_agent.types import ChatMessage


class MLXBackendTests(unittest.TestCase):
    @patch("local_lean_agent.backends.mlx.request_json")
    def test_formal_experiment_budgets_are_forwarded_without_clamping(self, request):
        request.return_value = {"choices": [{"message": {"content": "proof"},
                                             "finish_reason": "length"}]}
        backend = MLXBackend(MLXConfig(managed_server=False))
        backend._model_id = "test-model"
        for budget in (512, 1024, 2048):
            with self.subTest(budget=budget):
                result = backend.chat([ChatMessage("user", "prove")], max_tokens=budget,
                                      temperature=0.0, top_p=0.95)
                self.assertEqual(request.call_args.kwargs["payload"]["max_tokens"], budget)
                self.assertEqual(result.finish_reason, "length")

    @patch("local_lean_agent.backends.mlx.request_json")
    def test_chat_uses_openai_compatible_contract(self, request) -> None:
        request.return_value = {
            "model": "test-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "answer"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        }
        backend = MLXBackend(MLXConfig(managed_server=False))
        backend._model_id = "test-model"
        result = backend.chat(
            [ChatMessage("user", "hello")],
            max_tokens=20,
            temperature=0.1,
            top_p=0.9,
            extra={"chat_template_kwargs": {"enable_thinking": False}},
        )
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["model"], "test-model")
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(result.text, "answer")
        self.assertEqual(result.usage.total_tokens, 12)

    @patch.object(MLXBackend, "health_check", return_value=True)
    def test_managed_mode_refuses_occupied_endpoint(self, _health) -> None:
        backend = MLXBackend(MLXConfig(managed_server=True))
        with self.assertRaisesRegex(RuntimeError, "already using"):
            backend.load_model("test-model")

    @patch("local_lean_agent.backends.mlx.request_json")
    def test_thinking_only_length_response_is_an_empty_completion(self, request) -> None:
        request.return_value = {
            "model": "test-model",
            "choices": [
                {
                    "message": {"role": "assistant", "reasoning": "private"},
                    "finish_reason": "length",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }
        backend = MLXBackend(MLXConfig(managed_server=False))
        backend._model_id = "test-model"
        result = backend.chat(
            [ChatMessage("user", "hello")],
            max_tokens=20,
            temperature=0.1,
            top_p=0.9,
            extra={"chat_template_kwargs": {"enable_thinking": True}},
        )
        self.assertEqual(result.text, "")
        self.assertEqual(result.finish_reason, "length")


if __name__ == "__main__":
    unittest.main()
