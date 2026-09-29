// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$app/environment', () => ({ browser: true }));

import overlayLayer from './overlayLayer';
import { resetLayerStackForTests } from './layerStack';

beforeEach(() => {
	resetLayerStackForTests();
});

function wait(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('overlayLayer', () => {
	it('sets an inline z-index on mount', () => {
		const node = document.createElement('div');
		document.body.appendChild(node);

		overlayLayer(node);

		expect(Number(node.style.zIndex)).toBeGreaterThan(0);
	});

	it('gives a later-mounted overlay a higher z than an earlier one still open', () => {
		const first = document.createElement('div');
		const second = document.createElement('div');
		document.body.append(first, second);

		overlayLayer(first);
		overlayLayer(second);

		expect(Number(second.style.zIndex)).toBeGreaterThan(Number(first.style.zIndex));
	});

	it('re-asserts its z-index after something replaces the whole style attribute', async () => {
		const node = document.createElement('div');
		document.body.appendChild(node);
		overlayLayer(node);
		const assigned = node.style.zIndex;

		node.setAttribute('style', 'left: 10px;');
		await wait();

		expect(node.style.zIndex).toBe(assigned);
	});

	it('disconnects its observer and releases its layer on destroy', async () => {
		const node = document.createElement('div');
		document.body.appendChild(node);
		const action = overlayLayer(node);
		const assigned = node.style.zIndex;

		action.destroy();
		node.setAttribute('style', 'left: 10px;');
		await wait();

		expect(node.style.zIndex).toBe('');

		const other = document.createElement('div');
		document.body.appendChild(other);
		overlayLayer(other);
		expect(other.style.zIndex).toBe(assigned);
	});

	it('passes its tier argument through to acquire and release', () => {
		for (let i = 0; i < 5; i += 1) {
			overlayLayer(document.body.appendChild(document.createElement('div')));
		}

		const node = document.createElement('div');
		document.body.appendChild(node);
		const action = overlayLayer(node, 'tooltip');
		const assigned = Number(node.style.zIndex);
		expect(assigned).toBeGreaterThan(10000);

		action.destroy();

		const other = document.createElement('div');
		document.body.appendChild(other);
		overlayLayer(other, 'tooltip');
		expect(Number(other.style.zIndex)).toBe(assigned);
	});
});
