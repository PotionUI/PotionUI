// @vitest-environment jsdom
//
// The dropdown floats over cards that share its base surface token, so its
// panel must read as elevated (a solid, deeper surface plus the overlay
// shadow) rather than blending in — see AutocompleteDropdown.svelte.
import { describe, it, expect, vi } from 'vitest';

const { default: AutocompleteDropdown } = await import(
	'../../src/lib/components/AutocompleteDropdown.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const parentRef = document.createElement('div');
	document.body.appendChild(parentRef);
	createClassComponent({
		component: AutocompleteDropdown as any,
		target,
		props: {
			parentRef,
			suggestions: [
				{
					id: 'v1',
					category_id: 'c1',
					label: 'Golden hour',
					value: 'golden hour lighting',
					sort_order: 0,
					created_at: '',
					updated_at: ''
				}
			],
			onSelectCategory: vi.fn(),
			onSelectValue: vi.fn()
		}
	});
	// AutocompleteDropdown portals its whole panel onto document.body
	// (src/lib/actions/portal.ts) so `position: fixed` stays
	// viewport-relative; query from body, not `target`, which stays empty.
	return document.body;
}

describe('AutocompleteDropdown elevation', () => {
	it('gives the panel a deeper surface and the overlay shadow, not the floating one', () => {
		const body = mount();
		const panel = body.querySelector('[role="listbox"]')?.parentElement;
		expect(panel).toBeTruthy();
		expect(panel?.className).toContain('bg-surface-2');
		expect(panel?.className).toContain('shadow-overlay');
		expect(panel?.className).not.toContain('bg-surface-1');
		expect(panel?.className).not.toContain('shadow-floating');
	});
});
