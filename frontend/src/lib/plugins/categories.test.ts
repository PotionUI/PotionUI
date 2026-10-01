import { describe, it, expect } from 'vitest';
import { pluginCategories, pluginTypeLabel, resolveCategory } from './categories';

describe('plugin categories', () => {
	it('lists the agreed categories in order with their labels', () => {
		expect(pluginCategories.map((c) => [c.id, c.label])).toEqual([
			['backends', 'Backends & compute'],
			['sources', 'Model sources'],
			['steps', 'Generation steps'],
			['tools', 'Tools & pages'],
			['security', 'Sign-in & security'],
			['monitoring', 'Monitoring'],
			['developer', 'Developer'],
			['other', 'Other']
		]);
	});

	it('resolves a known id to its metadata', () => {
		expect(resolveCategory('security').label).toBe('Sign-in & security');
	});

	it('falls back to Other for missing, legacy and unknown ids', () => {
		for (const id of [undefined, null, '', 'generation', 'workflow', 'nope']) {
			expect(resolveCategory(id).id).toBe('other');
		}
	});

	it('gives every category an icon', () => {
		for (const category of pluginCategories) expect(category.icon).toBeTruthy();
	});

	it('describes the plugin type in plain words', () => {
		expect(pluginTypeLabel('full-stack')).toBe('Server and interface parts');
		expect(pluginTypeLabel('backend-only')).toBe('Server part only');
		expect(pluginTypeLabel('frontend-only')).toBe('Interface part only');
		expect(pluginTypeLabel(undefined)).toBe('Unknown');
	});
});
