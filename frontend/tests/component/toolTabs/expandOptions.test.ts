import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get, writable } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import type { EditorRun, EditorTaskInfo, ExpandFormat } from '$lib/services/api/editorTasks';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from '$lib/tool-tabs/image/generate/controller';
import { expandUi } from '$lib/tool-tabs/image/generate/expand';
import { createRunEngine } from '$lib/tool-tabs/image/generate/runEngine';
import { api } from '$lib/services/api/index';
import ExpandFixture from './ExpandFixture.svelte';

const FORMATS: ExpandFormat[] = [
	{ id: 'square', label: 'Square', ratio: '1:1', source: 'built_in', group_id: null },
	{ id: 'story', label: 'Story', ratio: '9:16', source: 'built_in', group_id: null },
	{ id: 'landscape', label: 'Landscape', ratio: '16:9', source: 'built_in', group_id: null },
	{ id: 'house.team', label: 'Team banner', ratio: '5:2', source: 'house', group_id: 'marketing' }
];

const run = (): EditorRun => ({ id: 'er_1', task: 'expand', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [] });

async function settle() {
	for (let i = 0; i < 40; i++) await Promise.resolve();
	flushSync();
}

function controller() {
	const stack: EditStackState<unknown> = createStack();
	const engine = createRunEngine(
		{ start: async () => run(), get: async () => run(), cancel: async () => run(), socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) },
		{ views: writable({}) }
	);
	const tasks: EditorTaskInfo[] = [];
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: () => undefined,
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: async () => ({ id: 'ed', revision: 1 }),
		landRegion: async () => null,
		areaSize: () => null,
		stepsBeneath: async () => [],
		engine,
		ports: {
			tasks: async () => tasks,
			sessions: async () => [],
			formulas: async () => [],
			schema: async () => ({ properties: {} }),
			setDefault: async () => undefined,
			clearDefault: async () => undefined,
			saveSession: async () => ({ id: 'n', name: 'N', version: 1, updated_at: null }),
			updateSession: async () => ({ id: 'n', name: 'N', version: 1, updated_at: null })
		}
	};
	return createGenerateController(deps);
}

describe('the Expand panel', () => {
	beforeEach(() => {
		expandUi.reset();
		expandUi.update((current) => ({ ...current, source: { width: 1024, height: 1024 } }));
		vi.spyOn(api, 'listExpandFormats').mockResolvedValue(FORMATS);
	});
	afterEach(() => {
		vi.restoreAllMocks();
		expandUi.reset();
	});

	it('lists the ready formats and the custom one with the size each gives', async () => {
		const view = mountComponent(ExpandFixture, { controller: controller() });
		await settle();

		const rows = [...document.body.querySelectorAll('[data-expand-format]')].map((row) => (row.textContent ?? '').replace(/\s+/g, ' ').trim());
		expect(rows).toEqual(['Square 1:1 ', 'Story 9:16 1024×1821', 'Landscape 16:9 1821×1024', 'Team banner 5:2 2560×1024', 'Custom 1024×1024'].map((row) => expect.stringContaining(row.trim())));
		view.destroy();
	});

	it('picks several formats and shows how many', async () => {
		const view = mountComponent(ExpandFixture, { controller: controller() });
		await settle();

		for (const id of ['story', 'landscape']) document.body.querySelector<HTMLButtonElement>(`[data-expand-format="${id}"]`)!.click();
		await settle();

		expect(get(expandUi).picks).toEqual(['story', 'landscape']);
		expect(document.body.querySelector('[data-expand-format="story"]')?.getAttribute('aria-pressed')).toBe('true');
		expect(document.body.querySelector('[data-expand-count]')?.textContent).toContain('2 of 6');
		view.destroy();
	});

	it('takes a custom size', async () => {
		const view = mountComponent(ExpandFixture, { controller: controller() });
		await settle();

		document.body.querySelector<HTMLButtonElement>('[data-expand-format="custom"]')!.click();
		await settle();
		const width = document.body.querySelector<HTMLInputElement>('[data-expand-custom] input[aria-label="Width"]')!;
		width.value = '2000';
		width.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();

		expect(get(expandUi).custom.width).toBe(2000);
		expect(document.body.querySelector('[data-expand-format="custom"]')?.textContent).toContain('2000×1024');
		view.destroy();
	});

	it('moves the picture on the canvas with the arrow keys and centres it again', async () => {
		expandUi.update((current) => ({ ...current, picks: ['story'] }));
		const view = mountComponent(ExpandFixture, { controller: controller() });
		await settle();

		const picture = document.body.querySelector<HTMLElement>('[data-expand-picture]')!;
		expect(parseFloat(picture.style.top)).toBeCloseTo(21.91, 1);
		picture.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', shiftKey: true, bubbles: true }));
		await settle();
		expect(get(expandUi).anchor.y).toBeCloseTo(0.4, 5);
		expect(get(expandUi).anchor.x).toBe(0.5);

		document.body.querySelector<HTMLButtonElement>('[data-expand-center]')!.click();
		await settle();
		expect(get(expandUi).anchor).toEqual({ x: 0.5, y: 0.5 });
		view.destroy();
	});

	it('says how many passes a large expansion takes', async () => {
		expandUi.update((current) => ({ ...current, picks: ['house.team'], source: { width: 1024, height: 1024 } }));
		const view = mountComponent(ExpandFixture, { controller: controller() });
		await settle();

		expect(document.body.querySelector('[data-expand-passes]')?.textContent).toContain('2 passes');
		view.destroy();
	});
});
