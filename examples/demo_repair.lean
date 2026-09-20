import Mathlib

-- Deliberately incorrect proof: demonstrate compiler-guided repair.
theorem demo_repair (x : ℝ) : 0 ≤ x ^ 2 := by
  linarith
