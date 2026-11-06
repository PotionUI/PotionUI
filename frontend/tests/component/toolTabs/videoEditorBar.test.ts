import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { resetMediaToolCatalog } from '$lib/media-tools/catalog';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';
import { EFFECT_CATALOG, LOOK_TOOL } from '../fixtures/effectCatalog';

const { default: VideoEditorTab } = await import('$lib/components/tool-tabs/video/VideoEditorTab.svelte');

const CLIP: MediaRef = { path: 'uploads/dock.mp4', relative_path: 'uploads/dock.mp4', url: '/uploads/dock.mp4', name: 'dock.mp4', type: 'video', item_id: 'v-1' };

function render(activeTool: string, phone = false) {
	const host: ToolTabHost = {
		tabId: 'tab-v',
		tool: 'video',
		source: CLIP,
		state: null,
		activeTool,
		run: null,
		hint: null,
		phone,
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	};
	const view = mountComponent(VideoEditorTab, { host });
	const video = view.target.querySelector<HTMLVideoElement>('video')!;
	Object.defineProperty(video, 'duration', { value: 8, configurable: true });
	video.dispatchEvent(new Event('loadedmetadata'));
	flushSync();
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const all = (sel: string) => Array.from(view.target.querySelectorAll<HTMLElement>(sel));
	const rows = () => all('[data-edit-stack] [data-stack-row]').map((row) => row.dataset.status);
	return { view, q, all, rows };
}

beforeEach(() => {
	vi.useFakeTimers();
	resetMediaToolCatalog([...EFFECT_CATALOG, LOOK_TOOL]);
});

afterEach(() => {
	vi.useRealTimers();
	resetMediaToolCatalog([]);
	document.body.innerHTML = '';
});

describe('video options bar', () => {
	it('puts In, Out and the length in the one header bar, with an icon-only rail', () => {
		const { q, all } = render('trim');
		expect(all('[data-editor-toolbar]')).toHaveLength(1);
		expect(q('[data-tool-rail]')?.getAttribute('data-tool-rail')).toBe('icons');
		expect(q('[data-options-bar] [data-bar-title]')?.textContent).toContain('Trim');
		expect(q<HTMLInputElement>('[data-options-bar] [data-bar-time="in"]')?.value).toBe('00:00.00');
		expect(q<HTMLInputElement>('[data-options-bar] [data-bar-time="out"]')?.value).toBe('00:08.00');
		expect(q('[data-options-bar] [data-bar-length]')?.textContent).toBe('8.00 s');
		expect(q('[data-options-panel] h2')?.firstChild?.textContent).toBe('Trim');
		expect(q('[data-options-panel] [data-options-apply]')).toBeNull();
	});

	it('typing an In point sets a pending trim that Apply trim commits', () => {
		const { q, rows } = render('trim');
		expect(q<HTMLButtonElement>('[data-options-actions] [data-options-apply]')?.disabled).toBe(true);
		const field = q<HTMLInputElement>('[data-options-bar] [data-bar-time="in"]')!;
		field.dispatchEvent(new FocusEvent('focus'));
		field.value = '00:01.20';
		field.dispatchEvent(new Event('input', { bubbles: true }));
		field.dispatchEvent(new FocusEvent('blur'));
		flushSync();
		expect(q<HTMLInputElement>('[data-options-bar] [data-bar-time="in"]')?.value).toBe('00:01.20');
		expect(q('[data-options-bar] [data-bar-length]')?.textContent).toBe('6.80 s');
		expect(rows()).toEqual(['original', 'pending']);
		const apply = q<HTMLButtonElement>('[data-options-actions] [data-options-apply]')!;
		expect(apply.disabled).toBe(false);
		expect(apply.textContent).toContain('Apply trim');
		apply.click();
		flushSync();
		expect(rows()).toEqual(['original', 'current']);
	});

	it('shows the aspect chips, orientation and output size for Crop', () => {
		const { q } = render('crop');
		expect(q('[data-options-bar] [data-bar-aspects]')).not.toBeNull();
		expect(q('[data-options-bar] [data-bar-orient="rotate-right"]')).not.toBeNull();
		expect(q('[data-options-bar] [data-bar-size]')?.textContent).toBe('1280×720');
	});
});

describe('video look and effect tray', () => {
	it('lists the looks first, then the effects, with Original at the start', () => {
		const { q, all } = render('effects');
		expect(q('[data-tray]')).not.toBeNull();
		expect(all('[data-tray-item]').map((tile) => tile.dataset.trayItem)).toEqual([
			'__original',
			'look:warm',
			'look:mine:reel',
			'look:smear',
			'fix_colors',
			'retro-tape.scanlines',
			'film_grain',
			'retro-tape.wobble'
		]);
		expect(all('[data-tray-group]').map((group) => group.dataset.trayGroup)).toEqual(['Original', 'Film', 'Mine', 'Art', 'Colour', 'Texture', 'More']);
	});

	it('has no tray for other tools', () => {
		expect(render('trim').q('[data-tray]')).toBeNull();
	});

	it('a click on a look selects it as the pending step and Add effect commits it', () => {
		const { q, rows } = render('effects');
		q<HTMLButtonElement>('[data-tray-item="look:warm"]')!.click();
		flushSync();
		expect(rows()).toEqual(['original', 'pending']);
		expect(q('[data-tray-item="look:warm"]')?.getAttribute('aria-selected')).toBe('true');
		expect(q('[data-bar-effect]')?.textContent).toBe('Look');
		expect(q('[data-options-bar] [data-property-slider="Strength"]')).not.toBeNull();
		const apply = q<HTMLButtonElement>('[data-options-actions] [data-options-apply]')!;
		expect(apply.textContent).toContain('Add effect');
		apply.click();
		flushSync();
		expect(rows()).toEqual(['original', 'current']);
		expect(q('[data-tray-item="look:warm"]')?.getAttribute('aria-selected')).toBe('true');
		expect(q('[data-options-actions] [data-options-apply]')).toBeNull();
	});

	it('picking another look replaces the pending one instead of stacking both', () => {
		const { q, rows } = render('effects');
		q<HTMLButtonElement>('[data-tray-item="look:warm"]')!.click();
		flushSync();
		q<HTMLButtonElement>('[data-tray-item="look:smear"]')!.click();
		flushSync();
		expect(rows()).toEqual(['original', 'pending']);
		expect(q('[data-tray-item="look:smear"]')?.getAttribute('aria-selected')).toBe('true');
		expect(q('[data-tray-item="look:warm"]')?.getAttribute('aria-selected')).toBe('false');
	});

	it('Original drops the pending pick', () => {
		const { q, rows } = render('effects');
		q<HTMLButtonElement>('[data-tray-item="film_grain"]')!.click();
		flushSync();
		expect(rows()).toEqual(['original', 'pending']);
		q<HTMLButtonElement>('[data-tray-item="__original"]')!.click();
		flushSync();
		expect(rows()).toEqual(['current']);
	});

	it('moves the strength in the bar into the applied effect live, one undo per drag', () => {
		const { q, rows } = render('effects');
		q<HTMLButtonElement>('[data-tray-item="look:warm"]')!.click();
		flushSync();
		q<HTMLButtonElement>('[data-options-actions] [data-options-apply]')!.click();
		flushSync();
		const range = q<HTMLInputElement>('[data-options-bar] [data-property-slider="Strength"] input')!;
		for (const value of ['0.8', '0.6', '0.4']) {
			range.value = value;
			range.dispatchEvent(new Event('input', { bubbles: true }));
			flushSync();
		}
		expect(rows()).toEqual(['original', 'current']);
		expect(q('[data-options-bar] [data-property-slider="Strength"] [data-property-value]')?.textContent).toBe('0.4');
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
		flushSync();
		expect(q('[data-options-bar] [data-property-slider="Strength"] [data-property-value]')?.textContent).toBe('1');
		expect(rows()).toEqual(['original', 'current']);
	});
});

describe('video phone layout', () => {
	it('puts the tray above a one-line bar with Add, and the tool strip at the bottom', () => {
		const { q } = render('effects', true);
		expect(q('[data-tool-rail="strip"]')).not.toBeNull();
		expect(q('[data-tray]')).not.toBeNull();
		expect(q('[data-options-bar="phone"]')).not.toBeNull();
		const tray = q('[data-tray]')!;
		const bar = q('[data-options-bar="phone"]')!;
		expect(tray.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('shows In, Out and Apply in the phone bar for Trim and no options panel', () => {
		const { q } = render('trim', true);
		expect(q('[data-options-bar="phone"] [data-bar-time="in"]')).not.toBeNull();
		expect(q('[data-options-bar="phone"] [data-bar-time="out"]')).not.toBeNull();
		expect(q('[data-options-bar="phone"] [data-options-apply]')).not.toBeNull();
		expect(q('[data-options-panel]')).toBeNull();
	});
});
