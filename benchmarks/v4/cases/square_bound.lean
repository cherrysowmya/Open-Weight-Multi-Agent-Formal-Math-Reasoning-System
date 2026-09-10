import Mathlib

theorem square_bound (x : ℝ) (hx : 0 ≤ x) (hx1 : x ≤ 1) :
    0 ≤ x^2 ∧ x^2 ≤ x := by
  constructor
  · positivity
  · linarith
