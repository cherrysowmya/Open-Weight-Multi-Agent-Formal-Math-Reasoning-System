import Mathlib

theorem my_v4 (x : ℝ) (hx : 0 ≤ x) (hx1 : x ≤ 1) :
    0 ≤ x^2 ∧ x^2 ≤ x := by
  constructor
  · positivity
  · linarith
