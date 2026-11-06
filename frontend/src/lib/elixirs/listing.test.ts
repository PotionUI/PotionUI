import { expect, test } from 'vitest';
import { POLL_IDLE_MS, POLL_MS, filterCounts, filterElixirs, pollDelay, primaryAction, statusText, stripLayout } from './listing';

const item = (name: string, status: string, extra: Record<string, unknown> = {}) => ({ id: name, name, status, ...extra });
const list = [
	item('Potion study', 'done', { status_at: 't' }),
	item('Hero loop', 'brewing'),
	item('Lookbook', 'attention'),
	item('Banners', 'awaiting'),
	item('Sketchbook', 'not_brewed'),
	item('Turntable', 'stopped', { status_at: 't' })
];

test('filters group the statuses the way the filter bar names them', () => {
	expect(filterElixirs(list, { filter: 'active' }).map((entry) => entry.name)).toStrictEqual(['Hero loop', 'Banners']);
	expect(filterElixirs(list, { filter: 'attention' }).map((entry) => entry.name)).toStrictEqual(['Lookbook']);
	expect(filterElixirs(list, { filter: 'done' }).map((entry) => entry.name)).toStrictEqual(['Potion study']);
	expect(filterElixirs(list, { filter: 'not_brewed' }).map((entry) => entry.name)).toStrictEqual(['Sketchbook']);
	expect(filterElixirs(list, { filter: 'all' }).length).toBe(6);
});

test('search ignores case and combines with the status filter', () => {
	expect(filterElixirs(list, { query: '  LOOP ' }).map((entry) => entry.name)).toStrictEqual(['Hero loop']);
	expect(filterElixirs(list, { query: 'loop', filter: 'done' })).toStrictEqual([]);
});

test('counts follow the search so the chips never promise more than the list shows', () => {
	expect(filterCounts(list)).toStrictEqual({ all: 6, active: 2, attention: 1, done: 1, not_brewed: 1 });
	expect(filterCounts(list, 'o')).toStrictEqual({ all: 4, active: 1, attention: 1, done: 1, not_brewed: 1 });
	expect(filterCounts(list, 'hero')).toStrictEqual({ all: 1, active: 1, attention: 0, done: 0, not_brewed: 0 });
});

test('an unknown status reads as not brewed', () => {
	expect(statusText(item('x', 'weird'), () => 'never')).toBe('Not brewed yet');
	expect(filterElixirs([item('x', 'weird')], { filter: 'not_brewed' }).length).toStrictEqual(1);
});

test('only finished states carry a time', () => {
	const ago = () => '2 h ago';
	expect(statusText(list[0], ago)).toBe('Done 2 h ago');
	expect(statusText(list[5], ago)).toBe('Stopped 2 h ago');
	expect(statusText(list[1], ago)).toBe('Brewing');
	expect(statusText(item('x', 'done'), ago)).toBe('Done');
	expect(statusText(list[3], ago)).toBe('Waiting for your choice');
});

test('the main action follows the state', () => {
	const labels = Object.fromEntries(list.map((entry) => [entry.status, primaryAction(entry)]));
	expect(labels.brewing.id).toBe('stop');
	expect(labels.brewing.variant).toBe('danger');
	expect(labels.awaiting.label).toBe('Choose outputs');
	expect(labels.not_brewed.label).toBe('Brew');
	expect(labels.done.label).toBe('Brew again');
	expect(labels.attention.label).toBe('Brew again');
	expect(labels.stopped.label).toBe('Brew again');
});

test('the strip fills five slots with tiles, a +N tile and placeholders', () => {
	const outputs = (count: number) => Array.from({ length: count }, (_, index) => ({ file_id: `f${index}` }));
	expect(stripLayout({ latest_outputs: [] })).toStrictEqual({ tiles: [], more: 0, placeholders: 5 });
	const two = stripLayout({ latest_outputs: outputs(2), output_count: 2 });
	expect(two.tiles.length).toBe(2);
	expect(two.placeholders).toBe(3);
	const five = stripLayout({ latest_outputs: outputs(5), output_count: 5 });
	expect([five.tiles.length, five.more, five.placeholders]).toStrictEqual([5, 0, 0]);
	const many = stripLayout({ latest_outputs: outputs(6), output_count: 12 });
	expect([many.tiles.length, many.more, many.placeholders]).toStrictEqual([4, 8, 0]);
});

test('polling is quick only while something brews and slow while an elixir just waits', () => {
	expect(pollDelay([item('a', 'brewing'), item('b', 'awaiting')])).toBe(POLL_MS);
	expect(pollDelay([item('b', 'awaiting'), item('c', 'done')])).toBe(POLL_IDLE_MS);
	expect(pollDelay([item('c', 'attention')])).toBe(POLL_IDLE_MS);
	expect(pollDelay([item('c', 'done'), item('d', 'not_brewed')])).toBe(null);
	expect(pollDelay(null)).toBe(null);
});
