import Mathlib

theorem v3_repair_injective_order {α β γ : Type} (f : α → β) (g : β → γ)
    (hf : Function.Injective f) (hg : Function.Injective g) :
    Function.Injective (g ∘ f) := by
  intro x y hxy
  exact hg (hf hxy)
