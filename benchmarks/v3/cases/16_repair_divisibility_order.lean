import Mathlib

theorem v3_repair_divisibility_order (a b c : ℕ) (hab : a ∣ b) (hbc : b ∣ c) : a ∣ c := by
  exact dvd_trans hbc hab
