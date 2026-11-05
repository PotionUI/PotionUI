import { describe, expect, it } from 'vitest';
import { introForMode, startersForMode } from './modeStarters';

const BUILTIN = ['generation', 'history', 'prompts', 'models', 'phrasebook'];

describe('startersForMode', () => {
	it('gives every builtin mode three complete starters', () => {
		for (const mode of BUILTIN) {
			const starters = startersForMode(mode);
			expect(starters).toHaveLength(3);
			for (const starter of starters) {
				expect(starter.title && starter.sub && starter.prompt && starter.icon).toBeTruthy();
			}
		}
	});

	it('keeps the image-oriented starters for the generation mode only', () => {
		expect(startersForMode('generation').map((s) => s.title)).toContain('Refine this prompt');
		for (const mode of BUILTIN.filter((id) => id !== 'generation')) {
			const text = startersForMode(mode)
				.map((s) => `${s.title} ${s.sub} ${s.prompt}`)
				.join(' ');
			expect(text).not.toContain('current image');
			expect(text).not.toContain('art directions');
		}
	});

	it('uses distinct titles per mode and a neutral set for unknown modes', () => {
		const titles = BUILTIN.flatMap((mode) => startersForMode(mode).map((s) => s.title));
		expect(new Set(titles).size).toBe(titles.length);
		expect(startersForMode('some-plugin-mode')).toHaveLength(3);
		expect(startersForMode(null)).toEqual(startersForMode('some-plugin-mode'));
		expect(startersForMode('some-plugin-mode')).not.toEqual(startersForMode('generation'));
	});
});

describe('introForMode', () => {
	it('gives History its own copy about finding, organising and editing files', () => {
		const intro = introForMode('history');
		expect(intro.heading).not.toBe(introForMode('generation').heading);
		expect(`${intro.heading} ${intro.body}`).not.toMatch(/making|shape prompts/);
		expect(intro.body).toMatch(/find/);
		expect(intro.body).toMatch(/edit/);
	});

	it('gives Prompts, Models and Phrasebook their own copy', () => {
		const generation = introForMode('generation');
		const seen = new Set([generation.heading]);
		for (const mode of ['history', 'prompts', 'models', 'phrasebook']) {
			const intro = introForMode(mode);
			expect(intro.heading && intro.body).toBeTruthy();
			expect(`${intro.heading} ${intro.body}`).not.toMatch(/making|shape prompts|workspace/);
			expect(seen.has(intro.heading)).toBe(false);
			seen.add(intro.heading);
		}
		expect(introForMode('prompts').body).toMatch(/prompts/);
		expect(introForMode('models').body).toMatch(/models/);
		expect(introForMode('phrasebook').body).toMatch(/phrasebook/);
	});

	it('keeps the workspace copy for generation and unknown modes', () => {
		expect(introForMode('generation').heading).toBe('What are we making?');
		expect(introForMode('some-plugin-mode')).toEqual(introForMode('generation'));
		expect(introForMode(null)).toEqual(introForMode('generation'));
	});
});

describe('conversation mode', () => {
	it('has its own starters and an intro that promises no tools or page', () => {
		const starters = startersForMode('conversation');
		expect(starters).toHaveLength(3);
		expect(starters).not.toEqual(startersForMode('some-plugin-mode'));
		const text = starters.map((s) => `${s.title} ${s.sub} ${s.prompt}`).join(' ');
		expect(text).not.toMatch(/page|settings|current image|saved prompts/);
		const intro = introForMode('conversation');
		expect(intro).not.toEqual(introForMode('generation'));
		expect(intro.body).toMatch(/no tools/);
	});
});
