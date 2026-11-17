import { describe, it, expect } from 'vitest';
import { applyAudienceVisibility, applyAudienceVisibilityToSchema } from './audienceFilter';

interface AudienceTestNode {
	name?: string;
	type?: string;
	label?: string;
	audience?: 'simple' | 'advanced';
	visible?: boolean;
	hidden_when_video_director?: boolean;
	children?: AudienceTestNode[];
	[key: string]: unknown;
}

describe('applyAudienceVisibility', () => {
	it('hides an advanced leaf field in simple mode', () => {
		const field: AudienceTestNode = { name: 'cfg_rescale', audience: 'advanced' as const };
		expect(applyAudienceVisibility(field, 'simple')).toBe(false);
		expect(field.visible).toBe(false);
	});

	it('keeps a simple leaf field visible in simple mode', () => {
		const field: AudienceTestNode = { name: 'prompt', audience: 'simple' as const };
		expect(applyAudienceVisibility(field, 'simple')).toBe(true);
		expect(field.visible).toBe(true);
	});

	it('shows everything in advanced mode', () => {
		const field: AudienceTestNode = { name: 'cfg_rescale', audience: 'advanced' as const };
		expect(applyAudienceVisibility(field, 'advanced')).toBe(true);
		expect(field.visible).toBe(true);
	});

	it('respects an upstream reaction that already hid the field', () => {
		const field: AudienceTestNode = { name: 'x', audience: 'simple' as const, visible: false };
		expect(applyAudienceVisibility(field, 'simple')).toBe(false);
		expect(field.visible).toBe(false);
	});

	it('collapses a container whose children are all advanced', () => {
		const tab: AudienceTestNode = {
			type: 'tab',
			label: 'Advanced',
			children: [
				{ name: 'a', audience: 'advanced' as const },
				{ name: 'b', audience: 'advanced' as const }
			]
		};
		expect(applyAudienceVisibility(tab, 'simple')).toBe(false);
		expect(tab.visible).toBe(false);
		expect(tab.children?.[0].visible).toBe(false);
	});

	it('keeps a container visible if at least one child survives', () => {
		const group: AudienceTestNode = {
			type: 'group',
			children: [
				{ name: 'a', audience: 'advanced' as const },
				{ name: 'b', audience: 'simple' as const }
			]
		};
		expect(applyAudienceVisibility(group, 'simple')).toBe(true);
		expect(group.visible).toBe(true);
		expect(group.children?.[0].visible).toBe(false);
		expect(group.children?.[1].visible).toBe(true);
	});

	it('handles nested containers (tabs > tab > group)', () => {
		const tabs: AudienceTestNode = {
			type: 'tabs',
			children: [
				{
					type: 'tab',
					label: 'Basics',
					children: [{ name: 'prompt', audience: 'simple' as const }]
				},
				{
					type: 'tab',
					label: 'Extras',
					children: [
						{
							type: 'group',
							children: [{ name: 'noise', audience: 'advanced' as const }]
						}
					]
				}
			]
		};
		expect(applyAudienceVisibility(tabs, 'simple')).toBe(true);
		expect(tabs.children?.[0].visible).toBe(true);
		expect(tabs.children?.[1].visible).toBe(false);
	});

	it('hides a hidden_when_video_director leaf when the Director is active', () => {
		const field: AudienceTestNode = { name: 'duration', hidden_when_video_director: true };
		expect(applyAudienceVisibility(field, 'simple', undefined, true)).toBe(false);
		expect(field.visible).toBe(false);
	});

	it('keeps a hidden_when_video_director leaf visible when the Director is inactive', () => {
		const field: AudienceTestNode = { name: 'duration', hidden_when_video_director: true };
		expect(applyAudienceVisibility(field, 'simple', undefined, false)).toBe(true);
		expect(field.visible).toBe(true);
	});

	it('leaves an ordinary field alone when the Director is active', () => {
		const field: AudienceTestNode = { name: 'prompt' };
		expect(applyAudienceVisibility(field, 'simple', undefined, true)).toBe(true);
		expect(field.visible).toBe(true);
	});

	it('forceVisibleNames reveals a hidden_when_video_director field even while the Director is active', () => {
		const field: AudienceTestNode = { name: 'duration', hidden_when_video_director: true };
		expect(applyAudienceVisibility(field, 'simple', new Set(['duration']), true)).toBe(true);
		expect(field.visible).toBe(true);
	});

	it('collapses a container whose only child is hidden_when_video_director while active', () => {
		const group: AudienceTestNode = {
			type: 'group',
			children: [{ name: 'duration', hidden_when_video_director: true }]
		};
		expect(applyAudienceVisibility(group, 'simple', undefined, true)).toBe(false);
		expect(group.visible).toBe(false);
	});
});

describe('applyAudienceVisibilityToSchema', () => {
	it('walks the root-wrapped schema shape and mutates in place', () => {
		const schema: { properties: Record<string, { children: AudienceTestNode[] }> } = {
			properties: {
				custom: {
					children: [
						{ name: 'prompt', audience: 'simple' as const },
						{ name: 'cfg_rescale', audience: 'advanced' as const }
					]
				}
			}
		};
		const result = applyAudienceVisibilityToSchema(schema, 'simple');
		expect(result).toBe(schema);
		expect(schema.properties.custom.children[0].visible).toBe(true);
		expect(schema.properties.custom.children[1].visible).toBe(false);
	});

	it('is a no-op for a null/malformed schema', () => {
		expect(applyAudienceVisibilityToSchema(null, 'simple')).toBeNull();
		expect(applyAudienceVisibilityToSchema({}, 'simple')).toEqual({});
	});
});
