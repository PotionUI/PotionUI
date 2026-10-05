import { describe, it, expect } from 'vitest';
import { parseRulesSummary, autoOrganizeHref } from './autoOrganizeCounts';

describe('parseRulesSummary', () => {
	it('reads per-subject active counts', () => {
		const result = parseRulesSummary({
			subjects: {
				generation: { active: 3, needs_attention: 1 },
				model: { active: 0 }
			}
		});
		expect(result.generations).toEqual({ active: 3, needsAttention: 1 });
		expect(result.models).toEqual({ active: 0, needsAttention: 0 });
		expect(result.uploads).toBeNull();
	});

	it('accepts an enveloped payload', () => {
		const result = parseRulesSummary({ success: true, data: { subjects: { upload: { active: 2 } } } });
		expect(result.uploads?.active).toBe(2);
	});

	it('hides everything for garbage', () => {
		expect(parseRulesSummary(null).generations).toBeNull();
		expect(parseRulesSummary({ subjects: { generations: { active: 'x' } } }).generations).toBeNull();
	});
});

describe('autoOrganizeHref', () => {
	it('links to the subject page', () => {
		expect(autoOrganizeHref('uploads')).toBe('/auto-organize?subject=uploads');
	});
});
