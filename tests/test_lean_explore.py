import unittest
from unittest.mock import Mock

from local_lean_agent.config import LeanExploreConfig

from local_lean_agent.retrieval.lean_explore import (
    LeanExploreProtocolError,
    LeanExploreMCPClient,
    _json_object,
    _mcp_text,
)


class LeanExploreTests(unittest.TestCase):
    def test_summary_and_source_are_combined_with_real_tool_counts(self) -> None:
        client = LeanExploreMCPClient(LeanExploreConfig(limit=2, source_limit=1))
        client.start = Mock()
        client.call_tool = Mock(side_effect=[
            '{"results":[{"id":1,"name":"List.append_nil","description":"Right identity"},{"id":2,"name":"List.nil_append"}],"processing_time_ms":12}',
            '{"source_text":"theorem append_nil (xs : List α) : xs ++ [] = xs"}',
        ])
        retrieved = client.retrieve("append empty")
        self.assertTrue(retrieved.available)
        self.assertEqual(retrieved.tool_calls, 2)
        self.assertEqual(retrieved.hits[0].name, "List.append_nil")
        self.assertIsNotNone(retrieved.hits[0].source_text)
        self.assertIsNone(retrieved.hits[1].source_text)
        self.assertEqual(client.call_tool.call_args_list[0].args[1]["rerank_top"], 0)
        self.assertEqual(client.call_tool.call_args_list[0].args[1]["packages"], ["Mathlib", "Init"])

    def test_malformed_search_payload_is_not_success(self) -> None:
        client = LeanExploreMCPClient(LeanExploreConfig())
        client.start = Mock()
        client.call_tool = Mock(return_value='{"results":"bad"}')
        retrieved = client.retrieve("append empty")
        self.assertFalse(retrieved.available)
        self.assertIn("invalid results", retrieved.error_message)

    def test_extracts_text_content_from_mcp_result(self) -> None:
        self.assertEqual(
            _mcp_text(
                {
                    "content": [
                        {"type": "text", "text": '{"results": []}'},
                        {"type": "image", "data": "ignored"},
                    ]
                }
            ),
            '{"results": []}',
        )

    def test_json_object_requires_an_object(self) -> None:
        self.assertEqual(_json_object('{"results": []}', "search"), {"results": []})
        with self.assertRaises(LeanExploreProtocolError):
            _json_object("[]", "search")
        with self.assertRaises(LeanExploreProtocolError):
            _json_object("not json", "search")


if __name__ == "__main__":
    unittest.main()
