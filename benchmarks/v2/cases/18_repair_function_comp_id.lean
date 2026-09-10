import Mathlib

theorem v2_repair_function_comp_id {α β : Type} (f : α → β) : f ∘ id = f := by
  exact Function.compose_identity_right f
