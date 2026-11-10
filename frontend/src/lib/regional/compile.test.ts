import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { compileMossPrompt } from './compile';
import type { Region } from './regions';

interface Case {
	name: string;
	caption: string;
	width: number;
	height: number;
	items: Region[];
	expected: string;
}

const fixture = JSON.parse(
	readFileSync(new URL('../../../../tests/fixtures/regional_prompt/moss_cases.json', import.meta.url), 'utf8')
) as { cases: Case[] };

const box = (caption: string, extra: Partial<Region> = {}): Region => ({ x: 0.25, y: 0.25, w: 0.5, h: 0.5, caption, ...extra });

describe('compileMossPrompt', () => {
	for (const c of fixture.cases) {
		it(`matches the backend fixture: ${c.name}`, () => {
			expect(compileMossPrompt(c.caption, c.items, c.width, c.height)).toBe(c.expected);
		});
	}

	it('strips a padded caption', () => {
		const out = JSON.parse(compileMossPrompt('p', [box('  A lighthouse  ')], 1024, 1024));
		expect(out.items[0].caption).toBe('A lighthouse');
	});

	it('strips a padded quoted span', () => {
		const out = JSON.parse(compileMossPrompt('p', [box('Title "  OPEN  "')], 1024, 1024));
		expect(out.items[0].label).toBe('text');
		expect(out.items[0].text).toBe('OPEN');
	});

	it('drops a box with no pixel area', () => {
		const out = compileMossPrompt('plain', [box('thin', { x: 1, w: 0.05 })], 1024, 1024);
		expect(out).toBe('plain');
		const mixed = JSON.parse(compileMossPrompt('p', [box('thin', { x: 1, w: 0.05 }), box('kept')], 1024, 1024));
		expect(mixed.items.map((i: { caption: string }) => i.caption)).toEqual(['kept']);
	});

	it('clamps a box that overhangs the canvas', () => {
		const out = JSON.parse(compileMossPrompt('p', [box('edge', { x: 0.9, w: 0.5 })], 1000, 1000));
		expect(out.items[0].bbox).toEqual([-250, 400, 250, 500]);
	});

	it('keeps non-ASCII characters and Python separators', () => {
		const out = compileMossPrompt('café', [box('naïve')], 1024, 1024);
		expect(out).toContain('"caption": "café"');
		expect(out).toContain('"bbox": [-256, -256, 256, 256]');
	});
});
