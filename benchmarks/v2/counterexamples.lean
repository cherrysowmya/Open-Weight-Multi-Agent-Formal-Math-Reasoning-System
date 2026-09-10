import Mathlib

-- Counterexample to the universally quantified successor fixed-point task.
example : (0 : ℕ) + 1 ≠ 0 := by
  decide

-- Counterexample to union = intersection for arbitrary sets.
example : ({0} : Set ℕ) ∪ ∅ ≠ ({0} : Set ℕ) ∩ ∅ := by
  simp
