import Mathlib

theorem v2_repair_set_union_empty {α : Type} (s : Set α) : s ∪ ∅ = s := by
  exact Set.empty_union s
