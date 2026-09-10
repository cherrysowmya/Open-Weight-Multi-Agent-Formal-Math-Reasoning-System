import Mathlib

theorem v3_repair_implication_order (P Q R : Prop) (hPQ : P → Q) (hQR : Q → R) : P → R := by
  intro hP
  exact hPQ (hQR hP)
