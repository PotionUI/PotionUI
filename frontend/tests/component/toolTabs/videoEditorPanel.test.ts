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

describe('video options panel', () => {
	it('puts In, Out and the length in the panel, the tool name in the title block, with an icon-only rail', () => {
		const { q, all } = render('trim');
		expect(all('[data-editor-toolbar]')).toHaveLength(1);
		expect(q('[data-tool-rail]')?.getAttribute('data-tool-rail')).toBe('icons');
		expect(q('[data-title-block] [data-title-name]')?.textContent).toBe('Trim');
		expect(q<HTMLInputElement>('[data-options-panel] [data-trim-field="in"]')?.value).toBe('00:00.00');
		expect(q<HTMLInputElement>('[data-options-panel] [data-trim-field="out"]')?.value).toBe('00:08.00');
		expect(q('[data-options-panel] [data-trim-output]')?.textContent).toBe('8.00 s');
		expect(q('[data-options-panel] h2')).toBeNull();
		expect(q<HTMLButtonElement>('[data-options-footer] [data-options-apply]')?.disabled).toBe(true);
	});

	it('typing an In point sets a pending trim that Apply trim commits', () => {
		const { q, rows } = render('trim');
		expect(q<HTMLButtonElement>('[data-options-footer] [data-options-apply]')?.disabled).toBe(true);
		const field = q<HTMLInputElement>('[data-options-panel] [data-trim-field="in"]')!;
		field.dispatchEvent(new FocusEvent('focus'));
		field.value = '00:01.20';
		field.dispatchEvent(new Event('input', { bubbles: true }));
		field.dispatchEvent(new FocusEvent('blur'));
		flushSync();
		expect(q<HTMLInputElement>('[data-options-panel] [data-trim-field="in"]')?.value).toBe('00:01.20');
		expect(q('[data-options-panel] [data-trim-output]')?.textContent).toBe('6.80 s');
		expect(rows()).toEqual(['original', 'pending']);
		const apply = q<HTMLButtonElement>('[data-options-footer] [data-options-apply]')!;
		expect(apply.disabled).toBe(false);
		expect(apply.textContent).toContain('Apply trim');
		apply.click();
		flushSync();
		expect(rows()).toEqual(['original', 'current']);
	});

	it('shows the aspect buttons and orientation in the panel and the output size in the status line for Crop', () => {
		const { q } = render('crop');
		expect(q('[data-options-panel] [role="group"][aria-label="Aspect"]')).not.toBeNull();
		expect(q('[data-options-panel] button[aria-label="Rotate right"]')).not.toBeNull();
		expect(q('[data-canvas-status] [data-status-extra]')?.textContent).toContain('Output 1280×720');
	});
});

describe('video looks panel', () => {
	const tiles = (all: (sel: string) => HTMLElement[]) => all('[data-look-item]').map((tile) => tile.dataset.lookItem);

	it('lists Original and the looks in groups, with the strength field above', async () => {
		const { q, all } = render('look');
		flushSync();
		expect(tiles(all)).toEqual(['__original', 'look:warm', 'look:mine:reel', 'look:smear']);
		expect(all('[data-look-group]').map((group) => group.dataset.lookGroup)).toEqual(['Film', 'Mine', 'Art']);
		const strength = q('[data-entry-form]')!;
		const list = q('[data-look-list]')!;
		expect(strength.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(q('[data-look-item="__original"]')?.getAttribute('aria-selected')).toBe('true');
	});

	it('has no looks list on other tools', () => {
		expect(render('trim').q('[data-look-list]')).toBeNull();
	});

	it('a click on a look is the pending step, with Reset only and no Apply', () => {
		const { q, rows } = render('look');
		q<HTMLButtonElement>('[data-look-item="look:warm"]')!.click();
		flushSync();
		expect(rows()).toEqual(['original', 'pending']);
		expect(q('[data-look-item="look:warm"]')?.getAttribute('aria-selected')).toBe('true');
		expect(q('[data-options-footer] [data-options-apply]')).toBeNull();
		expect(q<HTMLButtonElement>('[data-options-footer] [data-options-reset]')?.disabled).toBe(false);
	});

	it('picking another look replaces the pending one instead of stacking both', () => {
		const { q, rows } = render('look');
		q<HTMLButtonElement>('[data-look-item="look:warm"]')!.click();
		flushSync();
		q<HTMLButtonElement>('[data-look-item="look:smear"]')!.click();
		flushSync();
		expect(rows()).toEqual(['original', 'pending']);
		expect(q('[data-look-item="look:smear"]')?.getAttribute('aria-selected')).toBe('true');
		expect(q('[data-look-item="look:warm"]')?.getAttribute('aria-selected')).toBe('false');
	});

	it('Original drops the pending pick', () => {
		const { q, rows } = render('look');
		q<HTMLButtonElement>('[data-look-item="look:warm"]')!.click();
		flushSync();
		expect(rows()).toEqual(['original', 'pending']);
		q<HTMLButtonElement>('[data-look-item="__original"]')!.click();
		flushSync();
		expect(rows()).toEqual(['current']);
	});

	it('leaving the tool applies the pick, and strength then moves live in one undo step per drag', async () => {
		const { q, rows } = render('look');
		q<HTMLButtonElement>('[data-look-item="look:warm"]')!.click();
		flushSync();
		q<HTMLButtonElement>('[data-tool-rail] [data-tool="trim"]')!.click();
		flushSync();
		expect(rows()).toEqual(['original', 'current']);
		q<HTMLButtonElement>('[data-tool-rail] [data-tool="look"]')!.click();
		for (let i = 0; i < 40; i++) await Promise.resolve();
		flushSync();
		const range = q<HTMLInputElement>('[data-entry-options="look"] input[type="range"], [data-entry-options="look"] input[type="number"]')!;
		for (const value of ['0.8', '0.6', '0.4']) {
			range.value = value;
			range.dispatchEvent(new Event('input', { bubbles: true }));
			flushSync();
		}
		expect(rows()).toEqual(['original', 'current']);
		expect(q<HTMLInputElement>('[data-entry-options="look"] input[type="range"], [data-entry-options="look"] input[type="number"]')!.value).toBe('0.4');
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
		flushSync();
		expect(q<HTMLInputElement>('[data-entry-options="look"] input[type="range"], [data-entry-options="look"] input[type="number"]')!.value).toBe('1');
		expect(rows()).toEqual(['original', 'current']);
	});
});

describe('video phone layout', () => {
	it('shows the grouped tool strip at the bottom and the options sheet above it', () => {
		const { q } = render('look', true);
		expect(q('[data-tool-rail="strip"]')).not.toBeNull();
		const sheet = q('[data-options-panel="phone"]')!;
		const strip = q('[data-tool-rail="strip"]')!;
		expect(q('[data-options-bar="phone"]')).toBeNull();
		expect(sheet.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('shows In, Out, Output and Apply for Trim in the options sheet', () => {
		const { q } = render('trim', true);
		expect(q('[data-options-panel="phone"] [data-trim-field="in"]')).not.toBeNull();
		expect(q('[data-options-panel="phone"] [data-trim-field="out"]')).not.toBeNull();
		expect(q('[data-options-panel="phone"] [data-trim-output]')).not.toBeNull();
		expect(q('[data-options-panel="phone"] [data-options-apply]')).not.toBeNull();
	});
});
