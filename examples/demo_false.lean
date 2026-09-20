import Mathlib

-- Negative control: no candidate may be accepted without a Lean proof.
theorem demo_false (n : ℕ) : n + 1 = n := by
  sorry
