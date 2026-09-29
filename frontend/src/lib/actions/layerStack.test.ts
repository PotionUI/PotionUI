// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { acquireLayer, releaseLayer, resetLayerStackForTests } from './layerStack';

beforeEach(() => {
	resetLayerStackForTests();
});

afterEach(() => {
	document.documentElement.style.removeProperty('--z-overlay');
	document.documentElement.style.removeProperty('--z-toast');
	document.documentElement.style.removeProperty('--z-tooltip');
});

describe('acquireLayer', () => {
	it('reads its floor from the matching CSS custom property on :root', () => {
		document.documentElement.style.setProperty('--z-overlay', '5000');
		expect(acquireLayer('overlay')).toBe(5001);
	});

	it('falls back to a safe floor when the CSS custom property is missing', () => {
		expect(acquireLayer('overlay')).toBe(1001);
	});

	it('gives a modal opened from an open popover a higher z, in open order', () => {
		const popover = acquireLayer('overlay');
		const modal = acquireLayer('overlay');
		expect(modal).toBeGreaterThan(popover);
	});

	it('keeps the toast and tooltip tiers independent of the overlay tier', () => {
		acquireLayer('overlay');
		acquireLayer('overlay');
		const toast = acquireLayer('toast');
		const tooltip = acquireLayer('tooltip');
		expect(toast).toBeGreaterThan(9000);
		expect(tooltip).toBeGreaterThan(toast);
	});
});

describe('releaseLayer — bounded ordering', () => {
	it('never ratchets upward while a long-lived layer stays open', () => {
		const longLived = acquireLayer('overlay');
		const seen = new Set<number>();
		for (let i = 0; i < 500; i += 1) {
			const z = acquireLayer('overlay');
			seen.add(z);
			releaseLayer('overlay', z);
		}
		expect(seen.size).toBe(1);
		expect(Math.max(...seen)).toBe(longLived + 1);
	});

	it('resets the floor once every layer in the tier has closed', () => {
		const first = acquireLayer('overlay');
		releaseLayer('overlay', first);
		const second = acquireLayer('overlay');
		expect(second).toBe(first);
	});

	it('accounts for the highest still-open layer, not just the most recently opened one', () => {
		const a = acquireLayer('overlay');
		const b = acquireLayer('overlay');
		releaseLayer('overlay', b);
		const c = acquireLayer('overlay');
		expect(c).toBe(b);
	});

	it('ignores a release for a z that is not currently open', () => {
		const a = acquireLayer('overlay');
		releaseLayer('overlay', a + 999);
		const b = acquireLayer('overlay');
		expect(b).toBeGreaterThan(a);
	});

	it('stays above the highest still-open layer when a lower one is released out of order', () => {
		const lower = acquireLayer('overlay');
		const higher = acquireLayer('overlay');
		releaseLayer('overlay', lower);
		const next = acquireLayer('overlay');
		expect(next).toBeGreaterThan(higher);
	});
});
