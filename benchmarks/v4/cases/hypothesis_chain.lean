import Mathlib

theorem hypothesis_chain (P Q R : Prop) (f : P → Q) (g : Q → R) :
    (P → R) ∧ (P → Q ∧ R) := by
  sorry
