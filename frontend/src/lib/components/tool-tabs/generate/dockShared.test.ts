import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { writable } from 'svelte/store';
import { render } from 'svelte/server';
import RangeOptions from '$lib/components/tool-tabs/video/options/RangeOptions.svelte';
import { createGenerateController } from '$lib/tool-tabs/image/generate/controller';
import { createRunEngine } from '$lib/tool-tabs/image/generate/runEngine';
import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

function controller(editor?: 'image' | 'video') {
	const ports = { tasks: async () => [], sessions: async () => [], formulas: async () => [], schema: async () => null, setDefault: async () => undefined, clearDefault: async () => undefined, saveSession: vi.fn(), updateSession: vi.fn() };
	const engine = createRunEngine({ start: vi.fn(), get: vi.fn(), cancel: vi.fn(), socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) }, { views: writable({}) });
	return createGenerateController({
		getStack: () => ({ steps: [], cursor: 0, pending: null, seq: 0 }),
		setStack: () => undefined,
		source: () => ({ ref: null }),
		editLink: async () => null,
		landRegion: async () => null,
		areaSize: () => null,
		stepsBeneath: async () => [],
		ports: ports as never,
		engine,
		...(editor ? { editor } : {})
	});
}

describe('the generate dock is one implementation for both editors', () => {
	const image = read('../image/ImageEditorTab.svelte');
	const video = read('../video/VideoEditorTab.svelte');

	it('mounts the same dock parts from the shared folder in the image and the video editor', () => {
		for (const part of ['FloatingPrompt', 'CandidateStrip', 'DockStaleNotice']) {
			const path = `$lib/components/tool-tabs/generate/${part}.svelte`;
			expect(image).toContain(`from '${path}'`);
			expect(video).toContain(`from '${path}'`);
		}
		expect(video).toContain("from '$lib/tool-tabs/image/generate/wiring'");
		expect(video).toContain("from '$lib/tool-tabs/image/generate/context'");
	});

	it('keeps no dock of its own in the video editor folder', () => {
		const own = readFileSync(new URL('../video/options/RangeOptions.svelte', import.meta.url), 'utf8');
		for (const markup of ['data-floating-prompt', 'data-dock-row', 'data-candidate-strip', 'EditorPickers']) expect(own).not.toContain(markup);
	});

	it('reads the task of the editor it is in and not the image one', () => {
		for (const name of ['FloatingPrompt', 'GenerateSettings', 'GenerateOptions']) {
			expect(read(`./${name}.svelte`)).not.toContain("tasks.get('image'");
		}
	});

	it('knows which editor it serves', () => {
		expect(controller().editor).toBe('image');
		expect(controller('video').editor).toBe('video');
	});
});

describe('the range panel', () => {
	function host(range: { start: number; end: number } | null): VideoToolHost {
		return {
			video: { aiRange: range, clip: { width: 1280, height: 720, duration: 6.4 }, playhead: 2.4, setAiRange: vi.fn() }
		} as unknown as VideoToolHost;
	}

	it('shows the marked range as start and end fields in seconds and its length', () => {
		const out = render(RangeOptions, { props: { host: host({ start: 2, end: 4 }), tool: 'restore' } }).body;
		expect(out).toContain('data-range-card="marked"');
		expect(out).toMatch(/data-range-field="start"/);
		expect(out).toContain('value="2.00"');
		expect(out).toContain('value="4.00"');
		expect(out).toContain('2.00 s');
		expect(out).toContain('Only the marked range is regenerated');
	});

	it('says the whole clip is used when nothing is marked', () => {
		const out = render(RangeOptions, { props: { host: host(null), tool: 'restore' } }).body;
		expect(out).toContain('data-range-card="whole"');
		expect(out).toContain('Whole clip');
		expect(out).toContain('6.40 s');
		expect(out).not.toContain('data-range-field');
	});

	it('is the only place the range fields are, bound to the host range and nothing else', () => {
		const source = read('../video/options/RangeOptions.svelte');
		expect(source).toContain('video.setAiRange(');
		expect(source).toContain('withStart(');
		expect(source).toContain('withEnd(');
	});
});
