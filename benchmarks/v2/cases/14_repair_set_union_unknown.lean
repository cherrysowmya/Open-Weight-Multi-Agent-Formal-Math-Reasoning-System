import Mathlib

theorem v2_repair_set_union_unknown {α : Type} (s t : Set α) : s ∪ t = t ∪ s := by
  exact Set.union_commutative s t
