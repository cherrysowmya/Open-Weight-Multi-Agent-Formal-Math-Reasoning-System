import Mathlib

set_option maxHeartbeats 200000

theorem intro_logic_IffTactic_L05
  (P Q R S : Prop)
  (h1 : R ↔ S)
  (h2 : ¬((P → Q ∨ ¬S) ∧ (S ∨ P → ¬Q) → (S → Q)) ↔ P ∧ Q ∧ ¬S)
  : ¬((P → Q ∨ ¬R) ∧ (R ∨ P → ¬Q) → (R → Q)) ↔ P ∧ Q ∧ ¬R := by
  sorry
