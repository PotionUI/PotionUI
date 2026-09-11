import { describe, expect, it } from 'vitest';
import { classifyCardSelectEvent, resolveCardSelect } from './marquee';

function eventLike(mods: Partial<Pick<MouseEvent, 'shiftKey' | 'ctrlKey' | 'metaKey'>>): MouseEvent {
	return { shiftKey: false, ctrlKey: false, metaKey: false, ...mods } as MouseEvent;
}

describe('classifyCardSelectEvent', () => {
	it('is plain when there is no event', () => {
		expect(classifyCardSelectEvent(undefined)).toBe('plain');
	});

	it('is plain when no modifier is held', () => {
		expect(classifyCardSelectEvent(eventLike({}))).toBe('plain');
	});

	it('is shift when the shift key is held', () => {
		expect(classifyCardSelectEvent(eventLike({ shiftKey: true }))).toBe('shift');
	});

	it('is ctrl when the ctrl key is held', () => {
		expect(classifyCardSelectEvent(eventLike({ ctrlKey: true }))).toBe('ctrl');
	});

	it('is ctrl when the meta (cmd) key is held', () => {
		expect(classifyCardSelectEvent(eventLike({ metaKey: true }))).toBe('ctrl');
	});

	it('prefers shift over ctrl when both are held', () => {
		expect(classifyCardSelectEvent(eventLike({ shiftKey: true, ctrlKey: true }))).toBe('shift');
	});
});

describe('resolveCardSelect', () => {
	const order = ['a', 'b', 'c', 'd'];

	it('plain toggles the target and moves the anchor onto it', () => {
		expect(resolveCardSelect('plain', order, 'a', 'c', ['a'])).toEqual({
			selection: ['a', 'c'],
			nextAnchorId: 'c'
		});
	});

	it('ctrl toggles the target and moves the anchor onto it, same as plain', () => {
		expect(resolveCardSelect('ctrl', order, 'a', 'c', ['a'])).toEqual({
			selection: ['a', 'c'],
			nextAnchorId: 'c'
		});
	});

	it('plain deselects an already-selected target', () => {
		expect(resolveCardSelect('plain', order, 'b', 'b', ['a', 'b'])).toEqual({
			selection: ['a'],
			nextAnchorId: 'b'
		});
	});

	it('shift selects the range from the anchor and leaves the anchor unchanged', () => {
		expect(resolveCardSelect('shift', order, 'a', 'c', [])).toEqual({
			selection: ['a', 'b', 'c'],
			nextAnchorId: 'a'
		});
	});

	it('shift with no anchor falls back to a plain toggle, anchor stays null', () => {
		expect(resolveCardSelect('shift', order, null, 'c', ['a'])).toEqual({
			selection: ['a', 'c'],
			nextAnchorId: null
		});
	});
});
