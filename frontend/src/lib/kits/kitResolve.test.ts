import { describe, expect, it } from 'vitest';
import { makeItem, makeKit } from './fixtures';
import { expandKitCitations } from './kitResolve';
import type { KitDirectory } from './kitChips';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

const specs: PromptResourceSpec[] = [
	{ field: 'refs', kind: 'image', token: '<Picture @>' },
	{ field: 'refs', kind: 'video', token: '<Video @>' }
];

const mara = makeKit('Mara', 'mara', [
	makeItem('image', 'face'),
	makeItem('image', 'hat'),
	makeItem('audio', 'voice'),
	makeItem('text', 'look', { text: 'Warm and calm.' })
]);
const directory: KitDirectory = new Map([[mara.id, mara]]);
const marker = (itemId?: string) => `@[refs:kit:${mara.id}${itemId ? `/${itemId}` : ''}]`;

describe('expandKitCitations', () => {
	it('leaves text without kit citations alone', () => {
		expect(expandKitCitations('A woman @[refs:a.png]', specs, {}, directory)).toBe('A woman @[refs:a.png]');
	});

	it('numbers a cited picture after the pictures already in the field', () => {
		const values = { refs: [{ path: 'a.png', url: '/a.png' }, { kit: mara.id }] };
		expect(expandKitCitations(`${marker(mara.items[0].id)} walks`, specs, values, directory)).toBe('<Picture 2> walks');
	});

	it('hands a bare kit to the model as media first, then its text, and skips what the model does not take', () => {
		expect(expandKitCitations(`Cast: ${marker()}`, specs, {}, directory)).toBe('Cast: <Picture 1> <Picture 2> Warm and calm.');
	});

	it('gives the same item the same number when it is cited twice', () => {
		const text = `${marker(mara.items[1].id)} and ${marker(mara.items[1].id)}`;
		expect(expandKitCitations(text, specs, {}, directory)).toBe('<Picture 1> and <Picture 1>');
	});

	it('inlines a cited text item and drops a skipped audio item', () => {
		expect(expandKitCitations(`${marker(mara.items[3].id)}`, specs, {}, directory)).toBe('Warm and calm.');
		expect(expandKitCitations(`[${marker(mara.items[2].id)}]`, specs, {}, directory)).toBe('[]');
	});

	it('drops a citation of a deleted kit or item', () => {
		expect(expandKitCitations('x @[refs:kit:kt_ffffffff] y', specs, {}, directory)).toBe('x  y');
		expect(expandKitCitations(`x ${marker('it_deadbeef')} y`, specs, {}, directory)).toBe('x  y');
	});

	it('drops a deleted file', () => {
		const gone = makeKit('Gone', 'gone', [makeItem('image', 'face', { missing: true })]);
		const dir: KitDirectory = new Map([[gone.id, gone]]);
		expect(expandKitCitations(`@[refs:kit:${gone.id}/${gone.items[0].id}]`, specs, {}, dir)).toBe('');
	});
});
