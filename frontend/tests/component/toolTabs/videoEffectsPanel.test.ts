import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { fakeVideoHost } from '../helpers/videoHost';
import { railFor, railTools } from '$lib/editors/railTools';
import { ensureCoreVideoEditor, syncVideoEffectEntries } from '$lib/tool-tabs/video/entries';
import { EFFECT_CATALOG } from '../fixtures/effectCatalog';

const { default: VideoEntryPanel } = await import('$lib/components/tool-tabs/video/VideoEntryPanel.svelte');

beforeAll(() => {
	ensureCoreVideoEditor();
	syncVideoEffectEntries(EFFECT_CATALOG);
});

afterEach(() => {
	document.body.innerHTML = '';
});

async function tick() {
	for (let i = 0; i < 40; i++) await Promise.resolve();
	flushSync();
}

function panel(tool: string, overrides: Record<string, unknown> = {}, phone = false) {
	const fake = fakeVideoHost(EFFECT_CATALOG, overrides);
	const entry = railTools.get('video', tool)!;
	const view = mountComponent(VideoEntryPanel, { entry, host: fake.host, phone });
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	return { fake, view, q };
}

describe('the video rail', () => {
	it('lists the groups in the aligned order with one entry per tool', () => {
		const rail = railFor('video', { editor: 'video', hasSource: true });
		const core = rail.filter((group) => ['time', 'frame', 'sound', 'look', 'effects', 'still'].includes(group.id));
		expect(core.map((group) => group.id)).toEqual(['time', 'frame', 'sound', 'look', 'effects', 'still']);
		expect(rail.find((group) => group.id === 'time')!.tools.map((tool) => tool.id)).toEqual(['trim', 'split', 'speed']);
		expect(rail.find((group) => group.id === 'frame')!.tools.map((tool) => tool.id)).toEqual(['crop', 'resize']);
		expect(rail.find((group) => group.id === 'sound')!.tools.map((tool) => tool.id)).toEqual(['mute', 'volume', 'replace_audio']);
		expect(rail.find((group) => group.id === 'look')!.tools.map((tool) => tool.id).slice(0, 3)).toEqual(['look', 'lut', 'fix_colors']);
		expect(rail.find((group) => group.id === 'effects')!.tools.map((tool) => tool.id).slice(0, 4)).toEqual(['film_grain', 'vignette', 'ken_burns', 'fade']);
		expect(rail.find((group) => group.id === 'still')!.tools.map((tool) => tool.id)).toEqual(['frame']);
	});

	it('gives every entry its own glyph', () => {
		const icons = railFor('video', { editor: 'video', hasSource: true })
			.flatMap((group) => group.tools)
			.filter((tool) => ['trim', 'split', 'speed', 'crop', 'resize', 'mute', 'volume', 'replace_audio', 'look', 'lut', 'fix_colors', 'film_grain', 'vignette', 'ken_burns', 'fade', 'frame'].includes(tool.id))
			.map((tool) => tool.icon);
		expect(new Set(icons).size).toBe(icons.length);
	});

	it('puts catalog effects without an entry of their own in Effects', () => {
		const effects = railFor('video', { editor: 'video', hasSource: true }).find((group) => group.id === 'effects')!;
		expect(effects.tools.map((tool) => tool.id)).toContain('retro-tape.scanlines');
	});
});

describe('an effect entry panel', () => {
	it('shows the catalog params and the description, and sends the whole values for that effect', async () => {
		const { q, fake } = panel('retro-tape.scanlines');
		await tick();
		expect(q('[data-effect-description]')!.textContent).toContain('CRT lines');
		const input = q<HTMLInputElement>('input[type="range"], input[type="number"]')!;
		input.value = '5';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		const [name, args] = fake.calls.find((call) => call[0] === 'params')!;
		expect(name).toBe('params');
		expect((args as unknown[])[0]).toBe('retro-tape.scanlines');
	});

	it('never says where an effect came from', async () => {
		const { view } = panel('retro-tape.scanlines');
		await tick();
		const text = view.target.textContent!.toLowerCase();
		expect(text).not.toContain('plugin');
		expect(text).not.toContain('retro-tape');
		expect(view.target.innerHTML).not.toMatch(/title=/);
	});

	it('shows Preview 3 s only when the live preview falls back', async () => {
		expect(panel('film_grain').q('[data-preview-clip]')).toBeNull();
		const fallback = panel('film_grain', { showClip: true });
		await tick();
		expect(fallback.q('[data-preview-clip]')!.textContent).toContain('Preview 3 s');
		fallback.q<HTMLButtonElement>('[data-preview-clip]')!.click();
		expect(fallback.fake.calls).toContainEqual(['preview', undefined]);
	});

	it('explains the server measurement on Fix colours', async () => {
		const { q } = panel('fix_colors', { showClip: true });
		await tick();
		expect(q('[data-measure-note]')).not.toBeNull();
	});
});

describe('value entry panels', () => {
	it('Speed edits the one speed step and shows the output length', async () => {
		const { q, fake } = panel('speed');
		await tick();
		expect(q('[data-speed-output]')!.textContent).toContain('8.00 s');
		const input = q<HTMLInputElement>('input[type="range"], input[type="number"]')!;
		input.value = '1.25';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		const sent = fake.calls.filter((call) => call[0] === 'params').map((call) => (call[1] as [string, Record<string, unknown>]));
		expect(sent.at(-1)![0]).toBe('speed');
		expect(sent.at(-1)![1]).toMatchObject({ speed: 1.25, keep_pitch: true });
	});

	it('Volume lists level, fade in and fade out', async () => {
		const { view } = panel('volume');
		await tick();
		const titles = [...view.target.querySelectorAll('[data-field-type]')].map((el) => el.getAttribute('data-field-type'));
		expect(titles.length).toBeGreaterThanOrEqual(4);
	});

	it('Replace audio puts the track picker above its fields', async () => {
		const { view, q, fake } = panel('replace_audio');
		await tick();
		const track = q('[data-replace-track]')!;
		const form = q('[data-entry-form]')!;
		expect(track.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		q<HTMLButtonElement>('[data-replace-pick="upload"]')!.click();
		expect(fake.calls).toContainEqual(['audio', 'upload']);
		expect(view.target.textContent).toContain('No track picked');
	});

	it('Mute is a sentence only', async () => {
		const { q } = panel('mute');
		await tick();
		expect(q('[data-effect-description]')!.textContent).toContain('Removes the sound');
		expect(q('[data-entry-form]')).toBeNull();
	});

	it('Extract frame offers the two actions and no steps', async () => {
		const { q, fake } = panel('frame');
		await tick();
		q<HTMLButtonElement>('[data-frame-action="save"]')!.click();
		q<HTMLButtonElement>('[data-frame-action="open"]')!.click();
		expect(fake.calls).toEqual([['frame', 'save'], ['frame', 'open']]);
	});
});
