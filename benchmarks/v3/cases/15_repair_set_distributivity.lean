import Mathlib

theorem v3_repair_set_distributivity {α : Type} (s t u : Set α) :
    s ∩ (t ∪ u) = (s ∩ t) ∪ (s ∩ u) := by
  rfl
