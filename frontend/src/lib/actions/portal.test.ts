// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';

vi.mock('$app/environment', () => ({ browser: true }));

import portal from './portal';

describe('portal', () => {
	it('moves the node to be a direct child of <body>', () => {
		const host = document.createElement('div');
		const node = document.createElement('div');
		host.appendChild(node);
		document.body.appendChild(host);

		portal(node);

		expect(node.parentElement).toBe(document.body);
	});

	it('removes the node from the DOM on destroy', () => {
		const node = document.createElement('div');
		const action = portal(node);

		expect(node.parentElement).toBe(document.body);
		action.destroy();
		expect(node.parentElement).toBeNull();
	});

	it('never touches z-index — an opt-out element keeps its own class-driven z', () => {
		const node = document.createElement('div');
		node.className = 'fixed z-30';

		portal(node);

		expect(node.style.zIndex).toBe('');
	});
});
