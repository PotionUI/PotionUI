/**
 * Swaps the `q` (floating form) and `w` (floating workbench) overlays
 * instead of letting the keybindings stack a second overlay on top of the
 * first: pressing the other overlay's key closes the open one and opens the
 * one just pressed. Pressing an overlay's own key behaves exactly like its
 * plain toggle (`toggleFloatingForm`/`toggleFloatingWorkbench`) when the
 * other overlay isn't open.
 */
import { openFloatingForm, closeFloatingForm, toggleFloatingForm, type FloatingFormPatch, type FloatingFormState } from './floatingForm';
import {
	openFloatingWorkbench,
	closeFloatingWorkbench,
	toggleFloatingWorkbench,
	type FloatingWorkbenchPatch,
	type FloatingWorkbenchState
} from './floatingWorkbench';

export interface FloatingOverlaysState extends FloatingFormState, FloatingWorkbenchState {}

export type FloatingOverlaysPatch = Partial<FloatingFormPatch> & Partial<FloatingWorkbenchPatch>;

export function pressFloatingForm(state: FloatingOverlaysState): FloatingOverlaysPatch {
	if (state.workbenchFloating) {
		return { ...closeFloatingWorkbench(), ...openFloatingForm(state) };
	}
	return toggleFloatingForm(state);
}

export function pressFloatingWorkbench(state: FloatingOverlaysState): FloatingOverlaysPatch {
	if (state.formFloating) {
		return { ...closeFloatingForm(state), ...openFloatingWorkbench() };
	}
	return toggleFloatingWorkbench(state);
}
