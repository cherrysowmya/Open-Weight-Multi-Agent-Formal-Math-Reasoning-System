import Mathlib

theorem v3_repair_reverse_append {α : Type} (xs ys : List α) :
    (xs ++ ys).reverse = ys.reverse ++ xs.reverse := by
  rfl
