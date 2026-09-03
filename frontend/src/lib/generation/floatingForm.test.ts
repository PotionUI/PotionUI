import { describe, it, expect } from 'vitest';
import { openFloatingForm, closeFloatingForm, toggleFloatingForm } from './floatingForm';

describe('openFloatingForm', () => {
	it('folds the inline form and remembers it was unfolded', () => {
		const patch = openFloatingForm({ leftPanelCollapsed: false });
		expect(patch).toEqual({
			formFloating: true,
			leftPanelCollapsed: true,
			formFloatingRestoreCollapsed: false
		});
	});

	it('folds the inline form and remembers it was already folded', () => {
		const patch = openFloatingForm({ leftPanelCollapsed: true });
		expect(patch).toEqual({
			formFloating: true,
			leftPanelCollapsed: true,
			formFloatingRestoreCollapsed: true
		});
	});
});

describe('closeFloatingForm', () => {
	it('restores an unfolded form to unfolded', () => {
		const patch = closeFloatingForm({ formFloatingRestoreCollapsed: false });
		expect(patch).toEqual({
			formFloating: false,
			leftPanelCollapsed: false,
			formFloatingRestoreCollapsed: undefined
		});
	});

	it('restores a folded form to folded', () => {
		const patch = closeFloatingForm({ formFloatingRestoreCollapsed: true });
		expect(patch).toEqual({
			formFloating: false,
			leftPanelCollapsed: true,
			formFloatingRestoreCollapsed: undefined
		});
	});
});

describe('toggleFloatingForm', () => {
	it('opens when not floating, from unfolded', () => {
		const patch = toggleFloatingForm({ leftPanelCollapsed: false, formFloating: false });
		expect(patch).toEqual({
			formFloating: true,
			leftPanelCollapsed: true,
			formFloatingRestoreCollapsed: false
		});
	});

	it('opens when not floating, from folded', () => {
		const patch = toggleFloatingForm({ leftPanelCollapsed: true, formFloating: false });
		expect(patch).toEqual({
			formFloating: true,
			leftPanelCollapsed: true,
			formFloatingRestoreCollapsed: true
		});
	});

	it('closes and restores the pre-float fold state when already floating', () => {
		const opened = toggleFloatingForm({ leftPanelCollapsed: false, formFloating: false });
		const closed = toggleFloatingForm(opened);
		expect(closed).toEqual({
			formFloating: false,
			leftPanelCollapsed: false,
			formFloatingRestoreCollapsed: undefined
		});
	});

	it('round-trips open -> close back to a folded starting point', () => {
		const opened = toggleFloatingForm({ leftPanelCollapsed: true, formFloating: false });
		const closed = toggleFloatingForm(opened);
		expect(closed.leftPanelCollapsed).toBe(true);
		expect(closed.formFloating).toBe(false);
	});
});
