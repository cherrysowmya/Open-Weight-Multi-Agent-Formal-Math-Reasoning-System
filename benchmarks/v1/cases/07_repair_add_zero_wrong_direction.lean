import Mathlib

theorem repair_add_zero_wrong_direction (n : ℕ) : n + 0 = n := by
  exact Nat.zero_add n
