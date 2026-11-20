import { beforeAll, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { fakeVideoHost } from '../helpers/videoHost';
import { foldGroups } from '$lib/editors/railFold';
import { railFor, railTools, type EntryContext } from '$lib/editors/railTools';
import { mediaToolCatalog } from '$lib/media-tools/catalog';
import { ensureCoreVideoEditor, syncVideoEffectEntries, LIVE_ENTRIES } from '$lib/tool-tabs/video/entries';
import { SCENARIOS } from '$lib/tool-tabs/video/payloadScenarios';
import { runReference } from '$lib/tool-tabs/video/payloadFlow';
import fixtures from '$lib/tool-tabs/video/fixtures/video-payloads.json';
import { EFFECT_CATALOG, LOOK_TOOL } from '../fixtures/effectCatalog';

const { default: CropOptions } = await import('$lib/components/tool-tabs/video/options/CropOptions.svelte');
const { default: SplitOptions } = await import('$lib/components/tool-tabs/video/options/SplitOptions.svelte');

function plain(value: unknown): unknown {
	const copy = JSON.parse(JSON.stringify(value));
	for (const row of copy.rows) delete row.icon;
	return copy;
}

const CATALOG = [...EFFECT_CATALOG, LOOK_TOOL];

beforeAll(() => {
	ensureCoreVideoEditor();
	syncVideoEffectEntries(CATALOG);
});

function ctxOf(fake: ReturnType<typeof fakeVideoHost>, tool: string): EntryContext {
	return { host: fake.host, phone: false, tool, pending: get(fake.host.pending), stack: fake.stack, catalog: CATALOG };
}

const VALUE_ENTRIES = ['speed', 'resize', 'volume', 'replace_audio', ...LIVE_ENTRIES.filter((id) => id !== 'look'), 'look'];

describe('value entries hand the form values to the editor unchanged', () => {
	for (const scenario of SCENARIOS) {
		const step = scenario.steps[0];
		if (scenario.steps.length !== 1 || !VALUE_ENTRIES.includes(step.op)) continue;
		it(`${scenario.name}: ${step.op} writes the very params the reference flow compiled`, () => {
			const fake = fakeVideoHost(CATALOG);
			const entry = railTools.get('video', step.op)!;
			entry.onValues!(JSON.parse(JSON.stringify(step.params)), { commit: false }, ctxOf(fake, step.op));
			expect(fake.calls).toEqual([['params', [step.op, step.params]]]);
			const [, [, written]] = fake.calls[0] as [string, [string, Record<string, unknown>]];
			expect(plain(runReference({ name: scenario.name, steps: [{ op: step.op, params: written }] }))).toEqual(plain((fixtures as Record<string, unknown>)[scenario.name]));
		});
	}

	it('ignores the idle commit so a pause does not write twice', () => {
		const fake = fakeVideoHost(CATALOG);
		railTools.get('video', 'speed')!.onValues!({ speed: 2, keep_pitch: true }, { commit: true }, ctxOf(fake, 'speed'));
		expect(fake.calls).toEqual([]);
	});

	it('Resize sends the size as a number even when the select hands back text', () => {
		const fake = fakeVideoHost(CATALOG);
		railTools.get('video', 'resize')!.onValues!({ short: '480', quality: 'high' }, { commit: false }, ctxOf(fake, 'resize'));
		expect(fake.calls).toEqual([['params', ['resize', { short: 480, quality: 'high' }]]]);
	});

	it('Looks keeps the look out of its form and only offers the strength', () => {
		const fake = fakeVideoHost(CATALOG);
		const fields = (railTools.get('video', 'look')!.schema as (ctx: EntryContext) => Array<{ name?: string }>)(ctxOf(fake, 'look'));
		expect(fields.map((field) => field.name)).toEqual(['strength']);
	});

	it('shows the panel params of the tool, not of another one', () => {
		const fake = fakeVideoHost(CATALOG);
		fake.params.speed = { speed: 1.25, keep_pitch: false };
		expect(railTools.get('video', 'speed')!.values!(ctxOf(fake, 'speed'))).toEqual({ speed: 1.25, keep_pitch: false });
	});
});

describe('bespoke panels write the same params as before', () => {
	it('Crop rotates and flips with the rectangle carried along', () => {
		const fake = fakeVideoHost(CATALOG);
		fake.params.crop = { rect: { x: 0, y: 0, width: 0.5, height: 1 }, rotation: 0, flipH: false, flipV: false };
		const view = mountComponent(CropOptions, { host: fake.host });
		flushSync();
		view.target.querySelector<HTMLButtonElement>('button[aria-label="Rotate right"]')!.click();
		flushSync();
		const [, args] = fake.calls.find((call) => call[0] === 'params')!;
		const [op, params] = args as [string, { rotation: number; rect: { width: number; height: number } }];
		expect(op).toBe('crop');
		expect(params.rotation).toBe(90);
		expect(params.rect.width).toBe(1);
		expect(params.rect.height).toBe(0.5);
	});

	it('Crop shows the crop in pixels and its output size', () => {
		const fake = fakeVideoHost(CATALOG);
		fake.params.crop = { rect: { x: 0.125, y: 0, width: 0.75, height: 1 }, rotation: 0, flipH: false, flipV: false };
		const view = mountComponent(CropOptions, { host: fake.host });
		flushSync();
		expect(view.target.querySelector('[data-crop-output]')!.textContent).toBe('1440×1080');
	});

	it('Split uses the playhead', () => {
		const fake = fakeVideoHost(CATALOG, { playhead: 3.2 });
		const view = mountComponent(SplitOptions, { host: fake.host });
		view.target.querySelector<HTMLButtonElement>('[data-split-playhead]')!.click();
		expect(fake.calls).toEqual([['params', ['split', { at: 3.2 }]]]);
	});
});

describe('rail folding with the video entries', () => {
	const rail = () => railFor('video', { editor: 'video', hasSource: true });
	const inputs = () => rail().map((group) => ({ id: group.id, count: group.tools.length, fold: group.fold }));

	it('keeps every entry when the rail is tall', () => {
		expect(foldGroups(inputs(), 1100)).toEqual([]);
	});

	it('folds Effects first, then Look, Sound, Time and Frame, and never Still', () => {
		const order = foldGroups(inputs(), 1);
		expect(order.slice(0, 5)).toEqual(['effects', 'look', 'sound', 'time', 'frame']);
		expect(order).not.toContain('still');
		expect(foldGroups(inputs(), 700)).toEqual(['effects']);
	});

	it('counts the catalog effects that join a group', () => {
		expect(get(mediaToolCatalog)).toBeDefined();
		expect(rail().find((group) => group.id === 'effects')!.tools.length).toBeGreaterThan(4);
	});
});
