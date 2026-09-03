/**
 * State machine for the generation form's floating ("quick") overlay,
 * toggled by the `toggle_floating_form` keybinding (default `q`).
 *
 * The floating overlay always folds the inline form behind it, but must
 * restore the inline fold state exactly as it was before the overlay
 * opened — folded if it was folded, unfolded otherwise — whether the
 * overlay closes via the shortcut again, Esc, or the backdrop.
 */
export interface FloatingFormState {
	leftPanelCollapsed?: boolean;
	formFloating?: boolean;
	formFloatingRestoreCollapsed?: boolean;
}

export interface FloatingFormPatch {
	formFloating: boolean;
	leftPanelCollapsed: boolean;
	formFloatingRestoreCollapsed?: boolean;
}

export function openFloatingForm(state: FloatingFormState): FloatingFormPatch {
	return {
		formFloating: true,
		leftPanelCollapsed: true,
		formFloatingRestoreCollapsed: !!state.leftPanelCollapsed
	};
}

export function closeFloatingForm(state: FloatingFormState): FloatingFormPatch {
	return {
		formFloating: false,
		leftPanelCollapsed: !!state.formFloatingRestoreCollapsed,
		formFloatingRestoreCollapsed: undefined
	};
}

export function toggleFloatingForm(state: FloatingFormState): FloatingFormPatch {
	return state.formFloating ? closeFloatingForm(state) : openFloatingForm(state);
}
