import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { writable } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { markEditorExtensionsReady } from '$lib/editors';
import type { DetectionResult, EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { ensureCoreImageEditor } from '$lib/tool-tabs/image/coreEditor';
import { createGenerateController, type ControllerDeps } from '$lib/tool-tabs/image/generate/controller';
import { createRunEngine, runViews } from '$lib/tool-tabs/image/generate/runEngine';
import { maskArea } from '$lib/tool-tabs/image/maskOverlays';
import FixFacesFixture from './FixFacesFixture.svelte';

const offer: TaskOffer = { preset_id: 'sdxl', preset_name: 'SDXL', mode: 'editor:fix_faces', from_mode: 'fix_faces', mode_label: 'Fix faces', label: null, short_description: null, order: 0, region_strategy: 'crop', via: null, qualifies: true, reason: null, strength: { default: 0.35, min: 0.1, max: 0.8 } };
const task = { id: 'fix_faces', label: 'Fix faces and hands', icon: 'mode-refine-faces', region: 'required', prompt: 'none', fallback: null, offers: [offer], default: null } as EditorTaskInfo;
const run = (over: Partial<EditorRun> = {}): EditorRun => ({ id: 'er_1', task: 'fix_faces', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [], ...over });
const found: DetectionResult = {
	picture: { width: 1000, height: 800 },
	detections: [
		{ id: 'd1', kind: 'face', box: { x: 0.1, y: 0.1, width: 0.2, height: 0.25 }, score: 0.97 },
		{ id: 'd2', kind: 'hand', box: { x: 0.6, y: 0.5, width: 0.1, height: 0.1 }, score: 0.81 }
	]
};

async function settle() {
	for (let i = 0; i < 80; i++) await Promise.resolve();
	flushSync();
}

function build(detect: () => Promise<DetectionResult> = async () => found) {
	const stack: EditStackState<unknown> = createStack();
	const store = writable(stack);
	const start = vi.fn(async () => run());
	const engine = createRunEngine(
		{ start, get: async () => run(), cancel: async () => run({ status: 'cancelled' }), socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) },
		{ views: writable({}) }
	);
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => store.set(next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: async () => ({ id: 'ed', revision: 1 }),
		landRegion: vi.fn(async () => ({ item_id: 'region' })),
		areaSize: () => null,
		stepsBeneath: async () => [],
		engine,
		ports: {
			detect,
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
	return { controller: createGenerateController(deps), store, start, deps };
}

function mountOptions(t: ReturnType<typeof build>, phone = false) {
	mountComponent<Record<string, unknown>>(FixFacesFixture, { controller: t.controller, stack: t.store, phone });
	const q = <T extends Element = HTMLElement>(selector: string) => document.body.querySelector<T>(selector);
	const all = (selector: string) => Array.from(document.body.querySelectorAll<HTMLElement>(selector));
	const text = (selector: string) => (q(selector)?.textContent ?? '').replace(/\s+/g, ' ').trim();
	return { q, all, text };
}

beforeEach(() => {
	ensureCoreImageEditor();
	markEditorExtensionsReady();
	mediaToolCatalogRevision.set(1);
	maskArea.set(null);
});

afterEach(() => {
	maskArea.set(null);
	runViews.set({});
});

describe('Fix faces options', () => {
	it('lists what was found with Face and Hand badges, all ticked', async () => {
		const t = build();
		t.controller.use('fix_faces');
		const { q, all, text } = mountOptions(t);
		await settle();
		expect(all('[data-detection-row]')).toHaveLength(2);
		expect(text('[data-detection-row="d1"]')).toContain('Face');
		expect(text('[data-detection-row="d2"]')).toContain('Hand');
		expect(q<HTMLInputElement>('[data-detection-check="d1"]')?.checked).toBe(true);
		expect(text('[data-fix-faces-count]')).toBe('2 of 2 ticked');
		expect(all('[data-detection-box]')).toHaveLength(2);
	});

	it('skips a box from the list and from the canvas tag, and ticks it back', async () => {
		const t = build();
		t.controller.use('fix_faces');
		const { q, all, text } = mountOptions(t);
		await settle();
		q('[data-detection-check="d2"]')!.click();
		await settle();
		expect(text('[data-fix-faces-count]')).toBe('1 of 2 ticked');
		expect(q('[data-detection-box="d2"]')?.getAttribute('data-detection-on')).toBe('false');
		q('[data-detection-tag="d2"]')!.click();
		await settle();
		expect(q('[data-detection-box="d2"]')?.getAttribute('data-detection-on')).toBe('true');
		q('[data-fix-faces-none-button]')!.click();
		await settle();
		expect(all('[data-detection-on="false"]')).toHaveLength(2);
	});

	it('shows the strength with the default of the offer and writes a change', async () => {
		const t = build();
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		const { q, text } = mountOptions(t);
		await settle();
		expect(text('[data-fix-faces-strength-value]')).toBe('0.35');
		const range = q<HTMLInputElement>('[data-fix-faces-strength-input]')!;
		expect(range.min).toBe('0.1');
		expect(range.max).toBe('0.8');
		range.value = '0.6';
		range.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(text('[data-fix-faces-strength-value]')).toBe('0.60');
		expect(t.controller.strengthOf('fix_faces')).toBe(0.6);
	});

	it('shows an unavailable detector inline and keeps the hand-marking hint', async () => {
		const t = build(() => Promise.reject({ response: { status: 503, data: { detail: { error: 'detector_unavailable' } } } }));
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		const { q, text } = mountOptions(t);
		await settle();
		expect(text('[data-fix-faces-unavailable]')).toContain('not available');
		expect(text('[data-fix-faces-unavailable]')).toContain('by hand');
		expect(q('[data-detection-row]')).toBeNull();
	});

	it('says when nothing was found, and finds again', async () => {
		const detect = vi.fn(async () => ({ picture: { width: 10, height: 10 }, detections: [] }));
		const t = build(detect);
		t.controller.use('fix_faces');
		const { q, text } = mountOptions(t);
		await settle();
		expect(text('[data-fix-faces-none]')).toContain('No faces or hands found');
		q('[data-fix-faces-find]')!.click();
		await settle();
		expect(detect).toHaveBeenCalledTimes(2);
	});

	it('runs with only the ticked boxes and has no button of its own', async () => {
		const t = build();
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		const { q } = mountOptions(t);
		await settle();
		q('[data-detection-check="d2"]')!.click();
		await settle();
		expect(await t.controller.generate('fix_faces', false)).toBe(true);
		expect(t.deps.landRegion).toHaveBeenCalledWith(false, [found.detections[0].box]);
		expect(q('[data-fix-faces-run]')).toBeNull();
	});
});
