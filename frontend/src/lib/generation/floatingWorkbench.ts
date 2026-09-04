/**
 * State machine for the workbench's floating overlay, toggled by the
 * `toggle_floating_workbench` keybinding (default `w`).
 *
 * Unlike the floating form (`floatingForm.ts`), the workbench has no inline
 * fold state to remember and restore — the overlay simply mirrors
 * `workbenchFloating` on and off.
 */
export interface FloatingWorkbenchState {
	workbenchFloating?: boolean;
}

export interface FloatingWorkbenchPatch {
	workbenchFloating: boolean;
}

export function openFloatingWorkbench(): FloatingWorkbenchPatch {
	return { workbenchFloating: true };
}

export function closeFloatingWorkbench(): FloatingWorkbenchPatch {
	return { workbenchFloating: false };
}

export function toggleFloatingWorkbench(state: FloatingWorkbenchState): FloatingWorkbenchPatch {
	return state.workbenchFloating ? closeFloatingWorkbench() : openFloatingWorkbench();
}

export interface FloatingWorkbenchSize {
	width: number;
	height: number;
}

export interface FloatingWorkbenchDelta {
	dx: number;
	dy: number;
}

export interface FloatingWorkbenchBounds {
	minWidth: number;
	maxWidth: number;
	minHeight: number;
	maxHeight: number;
}

function clamp(value: number, min: number, max: number): number {
	return Math.min(Math.max(value, min), Math.max(min, max));
}

/**
 * Arithmetic behind the floating workbench's edge/corner resize handles.
 * A drag on the right edge only moves `dx`, the bottom edge only `dy`, the
 * corner both — clamped to the overlay's available space so the window can
 * never grow past it or shrink below a usable minimum.
 */
export function resizeFloatingWorkbench(
	current: FloatingWorkbenchSize,
	delta: FloatingWorkbenchDelta,
	bounds: FloatingWorkbenchBounds
): FloatingWorkbenchSize {
	return {
		width: clamp(current.width + delta.dx, bounds.minWidth, bounds.maxWidth),
		height: clamp(current.height + delta.dy, bounds.minHeight, bounds.maxHeight)
	};
}
