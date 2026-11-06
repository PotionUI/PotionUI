import { expect, test } from 'vitest';
import { doneCount, failedCount, progressSegments, settleTier } from './header';

function rig(widths: Record<string, number>, start: string, available: number) {
	let current = start;
	const applied: string[] = [];
	return {
		applied,
		run: () =>
			settleTier({
				start,
				available,
				apply: async (tier: string) => {
					current = tier;
					applied.push(tier);
				},
				measure: () => widths[current]
			})
	};
}

const WIDTHS = { wide: 1200, mid: 950, compact: 700, minimal: 400 };

test('a row that fits keeps the widest tier', async () => {
	expect(await rig(WIDTHS, 'wide', 1300).run()).toBe('wide');
});

test('a row that does not fit steps down until the content fits', async () => {
	expect(await rig(WIDTHS, 'wide', 1000).run()).toBe('mid');
	expect(await rig(WIDTHS, 'wide', 800).run()).toBe('compact');
	expect(await rig(WIDTHS, 'wide', 500).run()).toBe('minimal');
});

test('a row too small for any tier ends on the minimal one', async () => {
	expect(await rig(WIDTHS, 'wide', 100).run()).toBe('minimal');
});

test('a row that grew steps back up', async () => {
	expect(await rig(WIDTHS, 'minimal', 1300).run()).toBe('wide');
});

test('stepping up needs a margin of spare room', async () => {
	expect(await rig(WIDTHS, 'mid', 1210).run()).toBe('mid');
	expect(await rig(WIDTHS, 'mid', 1224).run()).toBe('wide');
});

test('the tier left on screen is the one returned', async () => {
	const probe = rig(WIDTHS, 'mid', 1000);
	const result = await probe.run();
	expect(probe.applied.at(-1)).toBe(result);
});

test('done steps are counted', () => {
	expect(doneCount([{ view: { status: 'done' } }, { view: { status: 'running' } }, { view: null }])).toBe(1);
});

test('failed steps are counted', () => {
	const steps = [{ view: { status: 'failed' } }, { view: { status: 'no_output' } }, { view: { status: 'done' } }, { view: null }];
	expect(failedCount(steps)).toBe(2);
});

test('the summary reads name, done of total and elapsed', () => {
	expect(progressSegments('Brew 4', 3, 6, 0, '01:13', 'wide')).toStrictEqual([
		{ text: 'Brew 4', tone: 'neutral' },
		{ text: '3/6', tone: 'neutral' },
		{ text: '01:13', tone: 'neutral' }
	]);
	expect(progressSegments('Brew 4', 6, 6, 0, '02:48', 'mid').map((part) => part.text)).toStrictEqual(['Brew 4', '6/6', '02:48']);
});

test('failed steps add a danger segment', () => {
	expect(progressSegments('Brew 4', 5, 6, 1, '02:48', 'wide')).toStrictEqual([
		{ text: 'Brew 4', tone: 'neutral' },
		{ text: '5/6', tone: 'neutral' },
		{ text: '1 failed', tone: 'danger' },
		{ text: '02:48', tone: 'neutral' }
	]);
});

test('the summary shortens at the compact tier and drops an empty elapsed', () => {
	expect(progressSegments('Brew 2', 1, 3, 0, '03:12', 'compact').map((part) => part.text)).toStrictEqual(['Brew 2', '03:12']);
	expect(progressSegments('Brew 2', 1, 3, 0, '', 'compact').map((part) => part.text)).toStrictEqual(['Brew 2']);
	expect(progressSegments('Brew 2', 1, 3, 2, '03:12', 'compact').map((part) => part.text)).toStrictEqual(['Brew 2', '2 failed', '03:12']);
});
