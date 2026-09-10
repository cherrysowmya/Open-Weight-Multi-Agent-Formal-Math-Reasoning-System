import unittest

from local_lean_agent.prompts import SYSTEM_PROMPT, repair_prompt


class RepairPromptTests(unittest.TestCase):
    def test_includes_case_sensitive_equality_symmetry_pattern(self) -> None:
        self.assertIn("`h.symm` or `Eq.symm h`", SYSTEM_PROMPT)

    def test_explains_redundant_tactic_diagnostic(self) -> None:
        prompt = repair_prompt(
            "theorem t : True := by sorry",
            "theorem t : True := by\n  trivial\n  trivial",
            "error: No goals to be solved",
        )
        self.assertIn("remove the redundant tactic", prompt)


if __name__ == "__main__":
    unittest.main()
