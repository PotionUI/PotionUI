import { describe, it, expect } from 'vitest';
import { segmentMenuActions, SEGMENT_ACTION_GROUPS } from './segmentActions';
import type { Segment } from '$lib/types/segments';

function segment(partial: Partial<Segment> = {}): Segment {
	return {
		id: 'seg-1',
		content: 'a lone lighthouse',
		type: 'content',
		chips: {},
		enabled: true,
		...partial
	} as Segment;
}

describe('segmentMenuActions', () => {
	it('includes the insert actions for a content segment', () => {
		const ids = segmentMenuActions(segment(), { index: 0, total: 2, segmentDisabled: false }).map(
			(a) => a.id
		);
		expect(ids).toEqual(
			expect.arrayContaining(['insertPhrasebook', 'insertVariable', 'insertChoice'])
		);
	});

	it('never includes remove — Delete stays outside the menu', () => {
		const ids = segmentMenuActions(segment(), { index: 0, total: 2, segmentDisabled: false }).map(
			(a) => a.id
		);
		expect(ids).not.toContain('remove');
	});

	it('disables moveUp at the first position and moveDown at the last', () => {
		const first = segmentMenuActions(segment(), { index: 0, total: 3, segmentDisabled: false });
		const last = segmentMenuActions(segment(), { index: 2, total: 3, segmentDisabled: false });
		expect(first.find((a) => a.id === 'moveUp')?.disabled).toBe(true);
		expect(first.find((a) => a.id === 'moveDown')?.disabled).toBe(false);
		expect(last.find((a) => a.id === 'moveDown')?.disabled).toBe(true);
		expect(last.find((a) => a.id === 'moveUp')?.disabled).toBe(false);
	});

	it('flips toggleDisabled between Disable and Enable', () => {
		const enabled = segmentMenuActions(segment(), { index: 0, total: 2, segmentDisabled: false });
		const disabled = segmentMenuActions(segment({ enabled: false }), {
			index: 0,
			total: 2,
			segmentDisabled: true
		});
		expect(enabled.find((a) => a.id === 'toggleDisabled')?.label).toBe('Disable');
		expect(disabled.find((a) => a.id === 'toggleDisabled')?.label).toBe('Enable');
	});

	it('disables the insert actions when the segment is disabled', () => {
		const actions = segmentMenuActions(segment({ enabled: false }), {
			index: 0,
			total: 2,
			segmentDisabled: true
		});
		expect(actions.find((a) => a.id === 'insertVariable')?.disabled).toBe(true);
	});

	it('omits insertSyntax when the preset has no prompt syntax for this mode', () => {
		const ids = segmentMenuActions(segment(), { index: 0, total: 2, segmentDisabled: false }).map((a) => a.id);
		expect(ids).not.toContain('insertSyntax');
	});

	it('includes insertSyntax when the preset declares prompt syntax', () => {
		const actions = segmentMenuActions(segment(), { index: 0, total: 2, segmentDisabled: false, hasPromptSyntax: true });
		expect(actions.find((a) => a.id === 'insertSyntax')).toMatchObject({ glyph: '/', disabled: false });
	});

	it('disables insertSyntax when the segment is disabled', () => {
		const actions = segmentMenuActions(segment({ enabled: false }), {
			index: 0,
			total: 2,
			segmentDisabled: true,
			hasPromptSyntax: true
		});
		expect(actions.find((a) => a.id === 'insertSyntax')?.disabled).toBe(true);
	});

	it('every action id appears in exactly one SEGMENT_ACTION_GROUPS group', () => {
		const actions = segmentMenuActions(segment(), {
			index: 0,
			total: 2,
			segmentDisabled: false,
			hasPromptSyntax: true
		});
		for (const action of actions) {
			const groupsContaining = SEGMENT_ACTION_GROUPS.filter((group) => group.includes(action.id));
			expect(groupsContaining).toHaveLength(1);
		}
	});
});
