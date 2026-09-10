import Mathlib

example : ¬ (True → False) := by
  simp

example : ¬ (∀ x : ℝ, x ^ 2 < 0) := by
  intro h
  have h0 := h 0
  norm_num at h0

example : ¬ (∀ a b : ℝ, a^2 = b^2 → a = b) := by
  intro h
  have bad := h 1 (-1) (by norm_num)
  norm_num at bad

example : ¬ (∀ x y : ℝ, x / y * y = x) := by
  intro h
  have bad := h 1 0
  norm_num at bad
