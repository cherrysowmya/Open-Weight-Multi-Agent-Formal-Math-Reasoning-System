import Mathlib

theorem v3_repair_conjunction_swap (P Q : Prop) (h : P ∧ Q) : Q ∧ P := by
  exact ⟨h.1, h.2⟩
