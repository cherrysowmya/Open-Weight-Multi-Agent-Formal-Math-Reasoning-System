import Mathlib

theorem v2_repair_abs_nonneg_unknown (x : ℝ) : 0 ≤ |x| := by
  exact Real.abs_nonnegative x
