import { describe, expect, it } from 'vitest';
import { groupProvenance } from './provenance';
import { collectionRulesHref, filledByLabel } from './collectionRules';
import type { OrganizeProvenance } from '$lib/types/organize';

function row(over: Partial<OrganizeProvenance>): OrganizeProvenance {
	return {
		rule_id: 'r1',
		rule_name: 'Krea landscapes',
		rule_deleted: false,
		run_id: 'run',
		action: 'add_to_collection',
		target_type: 'collection',
		target_id: 'c1',
		target_name: 'Landscapes',
		created_at: '2026-10-05T09:12:00+00:00',
		...over
	};
}

describe('groupProvenance', () => {
	it('groups rows of one rule and lists targets once', () => {
		const groups = groupProvenance([
			row({}),
			row({ action: 'add_tags', target_type: 'tag', target_name: 'landscape' }),
			row({ target_name: 'Landscapes' }),
			row({ rule_id: 'r2', rule_name: 'Other', target_name: 'Misc' })
		]);
		expect(groups).toHaveLength(2);
		expect(groups[0].targets).toEqual(['Landscapes', 'landscape']);
		expect(groups[1].ruleName).toBe('Other');
	});
});

describe('collection rules link', () => {
	it('words the count', () => {
		expect(filledByLabel(1)).toBe('Filled by 1 rule');
		expect(filledByLabel(3)).toBe('Filled by 3 rules');
	});

	it('links to the rule for one and to the page for many', () => {
		const a = { id: 'r1', name: 'A', status: 'active' as const };
		const b = { id: 'r2', name: 'B', status: 'active' as const };
		expect(collectionRulesHref('library', [a])).toBe('/auto-organize?subject=uploads&rule=r1');
		expect(collectionRulesHref('models', [a, b])).toBe('/auto-organize?subject=models');
	});
});
