import Mathlib

set_option maxHeartbeats 200000

theorem intro_logic_IffIntro_L06 (P Q R : Prop) (h : P ∨ Q ∨ R → ¬(P ∧ Q ∧ R)) : (P ∨ Q) ∨ R → ¬((P ∧ Q) ∧ R) := by
  sorry
