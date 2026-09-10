import Mathlib

theorem repair_zero_add_wrong_direction (n : ℕ) : 0 + n = n := by
  exact Nat.add_zero n
