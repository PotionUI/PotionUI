import { describe, expect, it } from 'vitest';
import type { Segment } from '$lib/types/segments';
import type { GenerationTab } from '$lib/types/tabs';
import { parseToolActions } from '$lib/utils/markdown';
import { selectPrompt } from '$lib/generation/promptBatch';
import {
	applyPromptListAction,
	applySegmentActionToPrompt,
	restoreSegmentInPrompt,
	undoPromptListAction
} from './promptActions';

type Fields = Pick<
	GenerationTab,
	'prompt' | 'negativePrompt' | 'promptSegments' | 'negativePromptSegments' | 'promptTabs' | 'activePromptTab'
>;

function segment(id: string, content: string): Segment {
	return { id, content, type: 'content' } as Segment;
}

function single(text = 'a village at noon'): Fields {
	return {
		prompt: text,
		negativePrompt: '',
		promptSegments: [segment('s1', text)],
		negativePromptSegments: [],
		promptTabs: undefined,
		activePromptTab: undefined
	};
}

function merge(tab: Fields, patch: Partial<Fields> | null): Fields {
	return { ...tab, ...(patch ?? {}) };
}

async function addPrompt(tab: Fields, name?: string, content = ''): Promise<Fields> {
	const result = await applyPromptListAction(tab, { type: 'add_prompt', name, content });
	return merge(tab, result!.patch);
}

describe('parseToolActions prompt actions', () => {
	it('parses each prompt list action', () => {
		const text = [
			'<tool_action type="add_prompt" name="Night version">a village at night</tool_action>',
			'<tool_action type="select_prompt" prompt="2"></tool_action>',
			'<tool_action type="remove_prompt" prompt="1"></tool_action>',
			'<tool_action type="rename_prompt" prompt="0" name="Day"></tool_action>'
		].join('\n');

		const { actions, cleanedText } = parseToolActions(text);

		expect(cleanedText).toBe('');
		expect(actions).toMatchObject([
			{ type: 'add_prompt', name: 'Night version', content: 'a village at night' },
			{ type: 'select_prompt', promptIndex: 2 },
			{ type: 'remove_prompt', promptIndex: 1 },
			{ type: 'rename_prompt', promptIndex: 0, name: 'Day' }
		]);
	});

	it('keeps malformed prompt actions as text', () => {
		const text = '<tool_action type="select_prompt"></tool_action><tool_action type="rename_prompt" prompt="1"></tool_action>';

		expect(parseToolActions(text).actions).toEqual([]);
	});

	it('carries the prompt target on a segment action', () => {
		const { actions } = parseToolActions(
			'<tool_action type="update_segment" segment_index="0" segment_id="s1" prompt="1">x</tool_action>'
		);

		expect(actions[0]).toMatchObject({ type: 'update_segment', segmentIndex: 0, segmentId: 's1', promptIndex: 1 });
	});

	it('leaves promptIndex off a segment action without a prompt attribute', () => {
		const { actions } = parseToolActions('<tool_action type="update_segment" segment_index="0" segment_id="s1">x</tool_action>');

		expect(actions[0].promptIndex).toBeUndefined();
	});
});

describe('applyPromptListAction', () => {
	it('adds a named prompt with text and keeps prompt 1 intact', async () => {
		const tab = single();

		const next = await addPrompt(tab, 'Night version', 'a village at night');

		expect(next.promptTabs).toHaveLength(2);
		expect(next.activePromptTab).toBe(1);
		expect(next.promptTabs![0].prompt).toBe('a village at noon');
		expect(next.promptTabs![1].name).toBe('Night version');
		expect(next.promptTabs![1].prompt).toBe('a village at night');
		expect(next.prompt).toBe('a village at night');
	});

	it('adds an empty prompt when no text is given', async () => {
		const next = await addPrompt(single());

		expect(next.promptTabs).toHaveLength(2);
		expect(next.promptTabs![1].prompt).toBe('');
	});

	it('undoes an add and restores the previous active prompt', async () => {
		const tab = single();
		const result = await applyPromptListAction(tab, { type: 'add_prompt', name: 'Night', content: 'n' });
		const added = merge(tab, result!.patch);

		const restored = merge(added, undoPromptListAction(added, result!.undo));

		expect(restored.promptTabs).toBeUndefined();
		expect(restored.prompt).toBe('a village at noon');
	});

	it('selects a prompt and undoes back to the previous one', async () => {
		const two = await addPrompt(single(), 'Night', 'n');
		const result = await applyPromptListAction(two, { type: 'select_prompt', promptIndex: 0, content: '' });
		const selected = merge(two, result!.patch);

		expect(selected.activePromptTab).toBe(0);
		expect(selected.prompt).toBe('a village at noon');

		const back = merge(selected, undoPromptListAction(selected, result!.undo));
		expect(back.activePromptTab).toBe(1);
		expect(back.prompt).toBe('n');
	});

	it('renames a prompt and undoes the rename', async () => {
		const two = await addPrompt(single(), 'Night', 'n');
		const result = await applyPromptListAction(two, { type: 'rename_prompt', promptIndex: 1, name: 'Dusk', content: '' });
		const renamed = merge(two, result!.patch);

		expect(renamed.promptTabs![1].name).toBe('Dusk');

		const back = merge(renamed, undoPromptListAction(renamed, result!.undo));
		expect(back.promptTabs![1].name).toBe('Night');
	});

	it('removes a prompt and restores it at its index on undo', async () => {
		const two = await addPrompt(single(), 'Night', 'n');
		const three = await addPrompt(selectedFirst(two), 'Dusk', 'd');
		const result = await applyPromptListAction(three, { type: 'remove_prompt', promptIndex: 1, content: '' });
		const removed = merge(three, result!.patch);

		expect(removed.promptTabs!.map((p) => p.name)).toEqual([undefined, 'Dusk']);

		const back = merge(removed, undoPromptListAction(removed, result!.undo));
		expect(back.promptTabs!.map((p) => p.name)).toEqual([undefined, 'Night', 'Dusk']);
		expect(back.activePromptTab).toBe(2);
	});

	it('undoes an add by identity after an earlier prompt was removed', async () => {
		const two = await addPrompt(single(), 'Night', 'n');
		const addResult = await applyPromptListAction(two, { type: 'add_prompt', name: 'Dusk', content: 'd' });
		const three = merge(two, addResult!.patch);
		const removeResult = await applyPromptListAction(three, { type: 'remove_prompt', promptIndex: 0, content: '' });
		const afterRemove = merge(three, removeResult!.patch);

		expect(afterRemove.promptTabs!.map((p) => p.name)).toEqual(['Night', 'Dusk']);

		const undone = merge(afterRemove, undoPromptListAction(afterRemove, addResult!.undo));

		expect(undone.promptTabs).toBeUndefined();
		expect(undone.prompt).toBe('n');
	});

	it('refuses to undo an add whose prompt was removed', async () => {
		const two = await addPrompt(single(), 'Night', 'n');
		const addResult = await applyPromptListAction(two, { type: 'add_prompt', name: 'Dusk', content: 'd' });
		const three = merge(two, addResult!.patch);
		const removeResult = await applyPromptListAction(three, { type: 'remove_prompt', promptIndex: 2, content: '' });
		const afterRemove = merge(three, removeResult!.patch);

		expect(undoPromptListAction(afterRemove, addResult!.undo)).toBeNull();
	});

	it('undoes a rename by identity after a remove shifted the indices', async () => {
		const two = await addPrompt(single(), 'Night', 'n');
		const three = await addPrompt(two, 'Dusk', 'd');
		const renameResult = await applyPromptListAction(three, { type: 'rename_prompt', promptIndex: 2, name: 'Dawn', content: '' });
		const renamed = merge(three, renameResult!.patch);
		const removeResult = await applyPromptListAction(renamed, { type: 'remove_prompt', promptIndex: 0, content: '' });
		const afterRemove = merge(renamed, removeResult!.patch);

		expect(afterRemove.promptTabs!.map((p) => p.name)).toEqual(['Night', 'Dawn']);

		const back = merge(afterRemove, undoPromptListAction(afterRemove, renameResult!.undo));
		expect(back.promptTabs!.map((p) => p.name)).toEqual(['Night', 'Dusk']);
	});

	it('refuses to undo a rename whose prompt was removed', async () => {
		const two = await addPrompt(single(), 'Night', 'n');
		const three = await addPrompt(two, 'Dusk', 'd');
		const renameResult = await applyPromptListAction(three, { type: 'rename_prompt', promptIndex: 2, name: 'Dawn', content: '' });
		const renamed = merge(three, renameResult!.patch);
		const removeResult = await applyPromptListAction(renamed, { type: 'remove_prompt', promptIndex: 2, content: '' });
		const afterRemove = merge(renamed, removeResult!.patch);

		expect(undoPromptListAction(afterRemove, renameResult!.undo)).toBeNull();
	});

	it('gives every prompt in a batch a distinct id', async () => {
		const three = await addPrompt(await addPrompt(single(), 'Night', 'n'), 'Dusk', 'd');
		const ids = three.promptTabs!.map((p) => p.id);

		expect(ids.every(Boolean)).toBe(true);
		expect(new Set(ids).size).toBe(3);
	});

	it('never removes the last prompt', async () => {
		expect(await applyPromptListAction(single(), { type: 'remove_prompt', promptIndex: 0, content: '' })).toBeNull();

		const two = await addPrompt(single(), 'Night', 'n');
		const one = merge(two, (await applyPromptListAction(two, { type: 'remove_prompt', promptIndex: 0, content: '' }))!.patch);
		expect(one.promptTabs).toBeUndefined();
		expect(await applyPromptListAction(one, { type: 'remove_prompt', promptIndex: 0, content: '' })).toBeNull();
	});

	it('rejects out-of-range targets and blank renames', async () => {
		const two = await addPrompt(single(), 'Night', 'n');

		expect(await applyPromptListAction(two, { type: 'select_prompt', promptIndex: 5, content: '' })).toBeNull();
		expect(await applyPromptListAction(two, { type: 'remove_prompt', promptIndex: 2, content: '' })).toBeNull();
		expect(await applyPromptListAction(two, { type: 'rename_prompt', promptIndex: 0, name: '  ', content: '' })).toBeNull();
	});

	it('stops adding past the batch cap', async () => {
		let tab = single();
		for (let i = 0; i < 19; i++) tab = await addPrompt(tab);

		expect(tab.promptTabs).toHaveLength(20);
		expect(await applyPromptListAction(tab, { type: 'add_prompt', content: '' })).toBeNull();
	});
});

function selectedFirst(tab: Fields): Fields {
	return merge(tab, selectPrompt(tab, 0));
}

describe('segment actions on another prompt', () => {
	it('updates a segment of a non-active prompt and leaves the active one alone', async () => {
		const two = await addPrompt(single(), 'Night', 'a village at night');
		const nightSegmentId = two.promptTabs![1].promptSegments[0].id;

		const result = await applySegmentActionToPrompt(
			two,
			0,
			{ type: 'update_segment', segmentIndex: 0, segmentId: 's1', content: 'a harbour at noon' }
		);
		const next = merge(two, result!.patch);

		expect(next.promptTabs![0].prompt).toBe('a harbour at noon');
		expect(next.promptTabs![1].promptSegments[0].id).toBe(nightSegmentId);
		expect(next.prompt).toBe('a village at night');
		expect(next.activePromptTab).toBe(1);
	});

	it('adds and deletes segments in another prompt, with undo', async () => {
		const two = await addPrompt(single(), 'Night', 'n');

		const added = await applySegmentActionToPrompt(two, 0, { type: 'add_segment', segmentIndex: -1, segmentId: '', content: 'moonlight' });
		const withAdded = merge(two, added!.patch);
		expect(withAdded.promptTabs![0].promptSegments).toHaveLength(2);

		const deleted = await applySegmentActionToPrompt(withAdded, 0, { type: 'delete_segment', segmentIndex: 1, segmentId: added!.segmentId, content: '' });
		const withDeleted = merge(withAdded, deleted!.patch);
		expect(withDeleted.promptTabs![0].promptSegments).toHaveLength(1);
		expect(deleted!.removal!.prompt).toBe(0);

		const restored = merge(withDeleted, restoreSegmentInPrompt(withDeleted, 0, deleted!.removal!));
		expect(restored.promptTabs![0].promptSegments.map((s) => s.content)).toEqual(['a village at noon', 'moonlight']);
	});

	it('is a no-op for an unknown prompt or segment', async () => {
		const two = await addPrompt(single(), 'Night', 'n');

		expect(await applySegmentActionToPrompt(two, 4, { type: 'update_segment', segmentIndex: 0, segmentId: 's1', content: 'x' })).toBeNull();
		expect(await applySegmentActionToPrompt(two, 0, { type: 'update_segment', segmentIndex: 9, segmentId: 'nope', content: 'x' })).toBeNull();
	});
});
