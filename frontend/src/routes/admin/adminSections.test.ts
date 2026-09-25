import { describe, it, expect } from 'vitest';
import { ADMIN_SECTIONS, ADMIN_PLUGIN_TAB_FALLBACK_ICON, adminSectionIcon } from './adminSections';

describe('adminSectionIcon', () => {
	it('resolves the registered icon for a known section', () => {
		expect(adminSectionIcon('settings')).toBe('settings');
		expect(adminSectionIcon('users')).toBe('group');
		expect(adminSectionIcon('llm')).toBe('model');
		expect(adminSectionIcon('backends')).toBe('server');
	});

	it('falls back to the generic plugin icon for an unknown key', () => {
		expect(adminSectionIcon('not-a-real-section')).toBe(ADMIN_PLUGIN_TAB_FALLBACK_ICON);
	});

	it('never reuses the same icon for two different sections', () => {
		const icons = ADMIN_SECTIONS.map((section) => section.icon);
		expect(new Set(icons).size).toBe(icons.length);
	});
});
