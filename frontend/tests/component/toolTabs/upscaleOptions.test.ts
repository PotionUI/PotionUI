import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get, writable } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { markEditorExtensionsReady } from '$lib/editors';
import type { EditorRun, EditorTaskInfo } from '$lib/services/api/editorTasks';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { ensureCoreImageEditor } from '$lib/tool-tabs/image/coreEditor';
import { createGenerateController, type ControllerDeps } from '$lib/tool-tabs/image/generate/controller';
import { generateDraft, type GenerateParams } from '$lib/tool-tabs/image/generate/generateStep';
import { createRunEngine, runViews } from '$lib/tool-tabs/image/generate/runEngine';
import UpscaleFixture from './UpscaleFixture.svelte';

const offer = { preset_id: 'up', preset_name: 'Tile Upscaler', mode: 'editor:upscale', from_mode: 'upscale', mode_label: 'Upscale', label: null, short_description: null, order: 0, region_strategy: 'whole', via: null, qualifies: true, reason: null };
const task = { id: 'upscale', label: 'Upscale', icon: 'mode-upscale', region: 'none', prompt: 'none', resizes: true, fallback: null, offers: [offer], default: null } as EditorTaskInfo;
const run = (over: Partial<EditorRun> = {}): EditorRun => ({ id: 'er_1', task: 'upscale', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [], ...over });

async function settle() {
	for (let i = 0; i < 60; i++) await Promise.resolve();
	flushSync();
}

function build() {
	let stack: EditStackState<unknown> = createStack();
	const store = writable(stack);
	const start = vi.fn(async () => run());
	const engine = createRunEngine(
		{ start, get: async () => run(), cancel: async () => run({ status: 'cancelled' }), socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) },
		{ views: writable({}) }
	);
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => {
			stack = next;
			store.set(next);
		},
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: async () => ({ id: 'ed', revision: 1 }),
		landRegion: async () => null,
		areaSize: () => null,
		pictureSize: () => ({ width: 1000, height: 500 }),
		stepsBeneath: async () => [],
		engine,
		ports: {
			tasks: async () => [task],
			sessions: async () => [],
			formulas: async () => [],
			schema: async () => ({ properties: {} }),
			setDefault: async () => undefined,
			clearDefault: async () => undefined,
			saveSession: async () => ({ id: 'n', name: 'n', version: 1, updated_at: null }),
			updateSession: async () => ({ id: 'n', name: 'n', version: 2, updated_at: null })
		}
	};
	return { controller: createGenerateController(deps), store, start, set: deps.setStack };
}

function mountOptions(t: ReturnType<typeof build>, phone = false) {
	mountComponent<Record<string, unknown>>(UpscaleFixture, { controller: t.controller, stack: t.store, size: { width: 1000, height: 500 }, phone });
	const q = <T extends Element = HTMLElement>(selector: string) => document.body.querySelector<T>(selector);
	const text = (selector: string) => (q(selector)?.textContent ?? '').replace(/\s+/g, ' ').trim();
	return { q, text };
}

beforeEach(() => {
	ensureCoreImageEditor();
	markEditorExtensionsReady();
	mediaToolCatalogRevision.set(1);
});

afterEach(() => runViews.set({}));

describe('Upscale options', () => {
	it('shows the picture, 2x as the starting factor and the result size', async () => {
		const t = build();
		t.controller.use('upscale');
		const { q, text } = mountOptions(t);
		await settle();
		expect(text('[data-upscale-source]')).toBe('1000×500');
		expect(text('[data-upscale-result]')).toBe('2000×1000');
		expect(q('[data-upscale-factor="2"]')?.getAttribute('aria-pressed')).toBe('true');
		expect(q<HTMLInputElement>('[data-upscale-width]')?.value).toBe('2000');
	});

	it('picks 4x and writes the choice for the run', async () => {
		const t = build();
		t.controller.use('upscale');
		const { q, text } = mountOptions(t);
		await settle();
		q('[data-upscale-factor="4"]')!.click();
		await settle();
		expect(text('[data-upscale-result]')).toBe('4000×2000');
		expect(get(t.controller.ui).options.upscale).toEqual({ scale: 4 });
	});

	it('keeps the shape when a width is typed and turns the choice into a target', async () => {
		const t = build();
		t.controller.use('upscale');
		const { q, text } = mountOptions(t);
		await settle();
		const width = q<HTMLInputElement>('[data-upscale-width]')!;
		width.value = '3000';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(q<HTMLInputElement>('[data-upscale-height]')?.value).toBe('1500');
		expect(text('[data-upscale-result]')).toBe('3000×1500');
		expect(get(t.controller.ui).options.upscale).toEqual({ target: { width: 3000, height: 1500 } });
	});

	it('says why a size cannot be started and disables Upscale', async () => {
		const t = build();
		t.controller.use('upscale');
		const { q, text } = mountOptions(t);
		await settle();
		const width = q<HTMLInputElement>('[data-upscale-width]')!;
		width.value = '800';
		width.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(text('[data-upscale-reason]')).toBe('Pick a size larger than the picture.');
		expect(q<HTMLButtonElement>('[data-upscale-run]')?.disabled).toBe(true);
	});

	it('starts the run from the panel once a setup is chosen', async () => {
		const t = build();
		t.controller.use('upscale');
		const { q } = mountOptions(t);
		await settle();
		q('[data-upscale-factor="4"]')!.click();
		await settle();
		expect(q<HTMLButtonElement>('[data-upscale-run]')?.disabled).toBe(false);
		q('[data-upscale-run]')!.click();
		await settle();
		expect(t.start).toHaveBeenCalledTimes(1);
		expect((t.start.mock.calls[0] as unknown[])[1]).toMatchObject({ scale: 4, region: null, prompt: null });
	});

	it('waits for a run that is still going', async () => {
		const t = build();
		t.controller.use('upscale');
		const { q } = mountOptions(t);
		await settle();
		const params = { v: 2, task: 'upscale', prompt: '', setup: { preset_id: 'up', mode: 'editor:upscale', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' }, area: { region: null, size: null }, candidates: 1, results: [], chosen: 0, seed: null, rect: null, soft: false, run: { id: 'er_1', status: 'running', error: null }, options: { scale: 2 } } as GenerateParams;
		const draft = generateDraft(params, 'Upscale', 'mode-upscale');
		t.set({ steps: [{ ...draft, id: 's1', hidden: false }], cursor: 1, pending: null, seq: 1 });
		await settle();
		expect(q<HTMLButtonElement>('[data-upscale-run]')?.disabled).toBe(true);
	});

	it('leaves the button to the dock on a phone', async () => {
		const t = build();
		t.controller.use('upscale');
		const { q } = mountOptions(t, true);
		await settle();
		expect(q('[data-upscale-run]')).toBeNull();
		expect(q('[data-upscale-factor="2"]')).not.toBeNull();
	});
});
