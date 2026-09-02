import { describe, it, expect } from 'vitest';
import { backendDetailTabsFor, isBackendDetailTab } from './backendDetailTabs';

describe('backendDetailTabsFor', () => {
	it('gives native.remote an Infrastructure and Models tab', () => {
		expect(backendDetailTabsFor('native.remote').map((t) => t.id)).toEqual([
			'overview',
			'infrastructure',
			'models',
			'stats'
		]);
	});

	it('gives native.local an Optimizations tab, no Infrastructure or Models', () => {
		expect(backendDetailTabsFor('native.local').map((t) => t.id)).toEqual(['overview', 'optimizations', 'stats']);
	});

	it('gives every other driver just Overview and Stats', () => {
		expect(backendDetailTabsFor('comfyui').map((t) => t.id)).toEqual(['overview', 'stats']);
		expect(backendDetailTabsFor('unknown-driver').map((t) => t.id)).toEqual(['overview', 'stats']);
	});
});

describe('isBackendDetailTab', () => {
	it('is true for a tab that belongs to the driver', () => {
		expect(isBackendDetailTab('native.remote', 'infrastructure')).toBe(true);
		expect(isBackendDetailTab('native.local', 'optimizations')).toBe(true);
	});

	it('is false for a tab that does not belong to the driver', () => {
		expect(isBackendDetailTab('comfyui', 'infrastructure')).toBe(false);
		expect(isBackendDetailTab('native.remote', 'optimizations')).toBe(false);
		expect(isBackendDetailTab('comfyui', 'optimizations')).toBe(false);
	});

	it('overview and stats belong to every driver', () => {
		for (const driver of ['native.remote', 'native.local', 'comfyui']) {
			expect(isBackendDetailTab(driver, 'overview')).toBe(true);
			expect(isBackendDetailTab(driver, 'stats')).toBe(true);
		}
	});
});
