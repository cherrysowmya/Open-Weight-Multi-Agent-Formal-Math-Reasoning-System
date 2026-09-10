import Mathlib

theorem repair_existential_witness (n : ℕ) : ∃ m : ℕ, m = n + 1 := by
  exact ⟨n, rfl⟩
