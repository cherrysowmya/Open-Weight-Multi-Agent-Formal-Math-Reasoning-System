import Mathlib

theorem repair_and_intro_order (P Q : Prop) (hp : P) (hq : Q) : P ∧ Q := by
  exact ⟨hq, hp⟩
