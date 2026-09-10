import Mathlib

theorem v2_repair_nat_gcd_unknown (a b : ℕ) : Nat.gcd a b = Nat.gcd b a := by
  exact Nat.gcd_commutative a b
