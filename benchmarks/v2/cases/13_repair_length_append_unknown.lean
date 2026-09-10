import Mathlib

theorem v2_repair_length_append_unknown {α : Type} (xs ys : List α) : (xs ++ ys).length = xs.length + ys.length := by
  exact List.append_length xs ys
