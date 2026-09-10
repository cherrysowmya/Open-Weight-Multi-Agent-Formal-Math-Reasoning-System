import Mathlib

theorem invalid_rewrite (x y : ℝ) :
    2 * x * y ≤ x^2 + y^2 := by
  have : (x - y) ^ 2 ≥ 0 := by positivity
  rw [sq_le (le_refl 0)] at this
  linarith
