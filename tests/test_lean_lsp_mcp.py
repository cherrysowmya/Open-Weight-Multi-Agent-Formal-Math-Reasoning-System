import unittest

from local_lean_agent.feedback.lean_lsp_mcp import (
    _as_diagnostics,
    _diagnostics_prefer_term_goal,
    _diagnostic_location,
    _last_nonempty_line,
    _lsp_diagnostic_range,
    _unhelpful_goal,
    _unhelpful_term_goal,
)


class LeanLSPMCPTests(unittest.TestCase):
    def test_extracts_kimina_line_and_column(self) -> None:
        self.assertEqual(
            _diagnostic_location(("4:19: error: tactic failed",)),
            (4, 19),
        )

    def test_missing_location_is_explicit(self) -> None:
        self.assertEqual(_diagnostic_location(("unsolved goals",)), (None, None))

    def test_decodes_list_returned_by_mcp_tool(self) -> None:
        self.assertEqual(
            _as_diagnostics('["first diagnostic", "second diagnostic"]'),
            ("first diagnostic", "second diagnostic"),
        )

    def test_preserves_plain_text_mcp_tool_output(self) -> None:
        self.assertEqual(_as_diagnostics("plain diagnostic"), ("plain diagnostic",))

    def test_extracts_multiline_lsp_diagnostic_range(self) -> None:
        self.assertEqual(
            _lsp_diagnostic_range(("l3c58-l6c13, severity: 1\nunsolved goals",)),
            (3, 58, 6, 13),
        )

    def test_finds_last_nonempty_candidate_line(self) -> None:
        self.assertEqual(_last_nonempty_line("theorem t := by\n  assumption\n\n"), 2)

    def test_rejects_misleading_or_invalid_goal_output(self) -> None:
        self.assertTrue(_unhelpful_goal("Not a valid goal position. Try elsewhere?"))
        self.assertTrue(_unhelpful_goal("Goals at:\nline\nno goals"))
        self.assertFalse(_unhelpful_goal("h : P\n⊢ Q"))

    def test_rejects_empty_term_goal_output(self) -> None:
        self.assertTrue(_unhelpful_term_goal("No term goal found."))
        self.assertTrue(_unhelpful_term_goal("Not a valid term goal position"))
        self.assertFalse(_unhelpful_term_goal("Term goal at:\nQ ∧ P"))

    def test_term_errors_prefer_term_goal_feedback(self) -> None:
        self.assertTrue(
            _diagnostics_prefer_term_goal(
                ("l4c3-l4c12, severity: 1\nUnknown constant `Real.nope`",)
            )
        )
        self.assertFalse(
            _diagnostics_prefer_term_goal(
                ("l4c3-l4c11, severity: 1\nlinarith failed",)
            )
        )


if __name__ == "__main__":
    unittest.main()
