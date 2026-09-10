import unittest
from unittest.mock import patch

from local_lean_agent.config import KiminaConfig
from local_lean_agent.types import FailureCategory
from local_lean_agent.verification.kimina import KiminaVerifier


class KiminaVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = KiminaVerifier(KiminaConfig())

    def test_rejects_sorry_without_network_call(self) -> None:
        with patch("local_lean_agent.verification.kimina.request_json") as request:
            result = self.verifier.verify("theorem x : True := by sorry", attempt_id="x")
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_category, FailureCategory.UNSAFE_PLACEHOLDER)
        request.assert_not_called()

    def test_rejects_unsound_declarations_without_network_call(self) -> None:
        unsafe_candidates = (
            "axiom fabricated : False",
            "private axiom fabricated : False",
            "constant fabricated : False",
        )
        for code in unsafe_candidates:
            with self.subTest(code=code):
                with patch(
                    "local_lean_agent.verification.kimina.request_json"
                ) as request:
                    result = self.verifier.verify(code, attempt_id="unsafe")
                self.assertFalse(result.valid)
                self.assertEqual(
                    result.failure_category, FailureCategory.UNSAFE_PLACEHOLDER
                )
                request.assert_not_called()

    @patch("local_lean_agent.verification.kimina.request_json")
    def test_accepts_clean_current_api_response(self, request) -> None:
        request.return_value = {
            "results": [{"id": "x", "time": 0.1, "response": {"messages": [], "sorries": []}}]
        }
        result = self.verifier.verify("#check Nat", attempt_id="x")
        self.assertTrue(result.valid)
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["snippets"][0]["code"], "#check Nat")

    @patch("local_lean_agent.verification.kimina.request_json")
    def test_classifies_unknown_identifier(self, request) -> None:
        request.return_value = {
            "results": [
                {
                    "id": "x",
                    "response": {
                        "messages": [
                            {
                                "severity": "error",
                                "pos": {"line": 2, "column": 3},
                                "data": "unknown identifier 'nope'",
                            }
                        ]
                    },
                }
            ]
        }
        result = self.verifier.verify("#check nope", attempt_id="x")
        self.assertEqual(result.failure_category, FailureCategory.UNKNOWN_IDENTIFIER)

    @patch("local_lean_agent.verification.kimina.request_json")
    def test_classifies_named_tactic_failure(self, request) -> None:
        request.return_value = {
            "results": [
                {
                    "id": "x",
                    "response": {
                        "messages": [
                            {
                                "severity": "error",
                                "data": "linarith failed to find a contradiction",
                            }
                        ]
                    },
                }
            ]
        }
        result = self.verifier.verify(
            "theorem x : False := by linarith", attempt_id="x"
        )
        self.assertEqual(result.failure_category, FailureCategory.TACTIC_FAILURE)

    @patch("local_lean_agent.verification.kimina.request_json")
    def test_classifies_tactic_failure_without_failed_keyword(self, request) -> None:
        request.return_value = {
            "results": [
                {
                    "id": "x",
                    "response": {
                        "messages": [
                            {
                                "severity": "error",
                                "data": "omega could not prove the goal",
                            }
                        ]
                    },
                }
            ]
        }
        result = self.verifier.verify(
            "theorem x : False := by omega", attempt_id="x"
        )
        self.assertEqual(result.failure_category, FailureCategory.TACTIC_FAILURE)

    @patch("local_lean_agent.verification.kimina.request_json")
    def test_classifies_expected_command_as_lean_syntax(self, request) -> None:
        request.return_value = {
            "results": [
                {
                    "id": "x",
                    "response": {
                        "messages": [
                            {
                                "severity": "error",
                                "data": "unexpected identifier; expected command",
                            }
                        ]
                    },
                }
            ]
        }
        result = self.verifier.verify("theorem x : True := by [", attempt_id="x")
        self.assertEqual(result.failure_category, FailureCategory.LEAN_SYNTAX)


if __name__ == "__main__":
    unittest.main()
