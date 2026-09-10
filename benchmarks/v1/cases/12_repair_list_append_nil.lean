import Mathlib

theorem repair_list_append_nil {α : Type} (xs : List α) : xs ++ [] = xs := by
  exact List.nil_append xs
