import Mathlib

set_option maxHeartbeats 200000

open BigOperators Real Nat Topology Rat

theorem mathd_numbertheory_447 :
  ∑ k ∈ Finset.filter (λ x => 3∣x) (Finset.Icc 1 49), (k % 10) = 78 := by
  sorry
