import Mathlib

theorem v2_repair_reverse_unknown {α : Type} (xs : List α) : xs.reverse.reverse = xs := by
  exact List.reverse_twice_identity xs
