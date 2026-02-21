import { create } from "zustand";

type View = "treemap" | "function" | "batch" | "events";

interface SelectionState {
  view: View;
  selectedFunctionId: number | null;
  selectedLibrary: string | null;
  setView: (view: View) => void;
  selectFunction: (id: number) => void;
  selectLibrary: (lib: string | null) => void;
}

export const useSelectionStore = create<SelectionState>((set) => ({
  view: "treemap",
  selectedFunctionId: null,
  selectedLibrary: null,
  setView: (view) => set({ view }),
  selectFunction: (id) => set({ selectedFunctionId: id, view: "function" }),
  selectLibrary: (lib) => set({ selectedLibrary: lib }),
}));
