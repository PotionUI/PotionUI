import { describe, it, expect } from 'vitest';
import { pressFloatingForm, pressFloatingWorkbench } from './floatingOverlays';

describe('pressFloatingForm', () => {
	it('closes the workbench and opens the form when the workbench is floating', () => {
		const patch = pressFloatingForm({ workbenchFloating: true, leftPanelCollapsed: false });
		expect(patch).toEqual({
			workbenchFloating: false,
			formFloating: true,
			leftPanelCollapsed: true,
			formFloatingRestoreCollapsed: false
		});
	});

	it('opens the form when nothing is floating', () => {
		const patch = pressFloatingForm({ leftPanelCollapsed: false, formFloating: false });
		expect(patch).toEqual({
			formFloating: true,
			leftPanelCollapsed: true,
			formFloatingRestoreCollapsed: false
		});
	});

	it('closes the form when only the form is floating', () => {
		const patch = pressFloatingForm({ formFloating: true, formFloatingRestoreCollapsed: true });
		expect(patch).toEqual({
			formFloating: false,
			leftPanelCollapsed: true,
			formFloatingRestoreCollapsed: undefined
		});
	});
});

describe('pressFloatingWorkbench', () => {
	it('closes the form (restoring its fold state) and opens the workbench when the form is floating', () => {
		const patch = pressFloatingWorkbench({ formFloating: true, formFloatingRestoreCollapsed: false });
		expect(patch).toEqual({
			formFloating: false,
			leftPanelCollapsed: false,
			formFloatingRestoreCollapsed: undefined,
			workbenchFloating: true
		});
	});

	it('opens the workbench when nothing is floating', () => {
		const patch = pressFloatingWorkbench({ workbenchFloating: false });
		expect(patch).toEqual({ workbenchFloating: true });
	});

	it('closes the workbench when only the workbench is floating', () => {
		const patch = pressFloatingWorkbench({ workbenchFloating: true });
		expect(patch).toEqual({ workbenchFloating: false });
	});
});

describe('q/w round trip', () => {
	it('swapping back and forth ends with only the last-pressed overlay open', () => {
		let state: Record<string, unknown> = { leftPanelCollapsed: false };
		state = { ...state, ...pressFloatingWorkbench(state) };
		expect(state.workbenchFloating).toBe(true);
		expect(state.formFloating).toBeUndefined();

		state = { ...state, ...pressFloatingForm(state) };
		expect(state.workbenchFloating).toBe(false);
		expect(state.formFloating).toBe(true);

		state = { ...state, ...pressFloatingWorkbench(state) };
		expect(state.formFloating).toBe(false);
		expect(state.workbenchFloating).toBe(true);
	});
});
