import { markEditorExtensionsReady } from '$lib/editors/ready';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import { catalogDraft } from '$lib/tool-tabs/image/steps';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const SERVER_SENTENCE = 'Crop to subject needs an image with transparency';

const log = vi.hoisted(() => ({ previews: 0, refuseFirst: false }));

const refusal = vi.hoisted(() => ({
	message: 'Request failed with status code 400',
	response: {
		status: 400,
		data: {
			detail: {
				success: false,
				error: 'unsupported_media',
				message: 'Crop to subject needs an image with transparency',
				step_index: 0
			}
		}
	}
}));

vi.mock('$lib/media/loadImage', () => ({
	loadImage: async () => ({ naturalWidth: 8, naturalHeight: 6 })
}));

vi.mock('$lib/paint/loadPicked', async (original) => {
	const actual = await original<typeof import('$lib/paint/loadPicked')>();
	return {
		...actual,
		canvasFromBlob: async () => {
			const canvas = document.createElement('canvas');
			canvas.width = 480;
			canvas.height = 480;
			return canvas;
		}
	};
});

vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: async () => [],
	startMediaToolRun: async () => {
		throw new Error('The edit could not be started');
	},
	getMediaToolRun: async () => {
		throw new Error('no run');
	},
	cancelMediaToolRun: async () => null,
	previewFrame: async () => {
		log.previews += 1;
		if (log.previews === 1 && !log.refuseFirst) return { blob: new Blob(['png']), size: { width: 480, height: 480 } };
		throw refusal;
	},
	uploadMediaToolArtifact: async () => {
		throw new Error('The file is larger than your plan allows');
	}
}));

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

const SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/dusk.png',
	url: '/api/media/generations/gen-1/dusk.png',
	name: 'dusk.png',
	type: 'image',
	generation_file_id: '501'
};

function subjectState() {
	const stack = apply(beginEdit(createStack(), catalogDraft('subject', { mode: 'crop' })));
	return writeImageState({ ...emptyImageState('subject'), stack });
}

async function settle() {
	for (let round = 0; round < 4; round++) {
		for (let i = 0; i < 12; i++) await Promise.resolve();
		vi.advanceTimersByTime(100);
		flushSync();
	}
}

describe('ImageEditorTab preview failure', () => {
	beforeEach(() => {
		markEditorExtensionsReady();
		vi.useFakeTimers();
		log.previews = 0;
		log.refuseFirst = false;
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('shows the sentence the server sent when a step is refused', async () => {
		const host = {
			tabId: 'tab-1',
			tool: 'image',
			source: SOURCE,
			state: subjectState(),
			activeTool: 'subject',
			run: null,
			phone: false,
			setState: () => undefined,
			setDirty: () => undefined,
			setTitle: () => undefined,
			setThumbnail: () => undefined,
			setSource: () => undefined,
			replaceSource: () => undefined,
			setActiveTool: () => undefined,
			setRun: () => undefined,
			setActions: () => undefined,
			openToolTab: () => 'tab-2'
		} as unknown as ToolTabHost;
		const view = mountComponent(ImageEditorTab, { host });
		await settle();
		const display = view.target.querySelector<HTMLCanvasElement>('[data-editor-layer]');
		expect(display).not.toBeNull();
		expect(log.previews).toBe(1);
		expect(view.target.querySelector('[data-render-error]')).toBeNull();
		display!.dispatchEvent(new Event('contextrestored'));
		await settle();
		expect(log.previews).toBe(2);
		const shown = view.target.querySelector('[data-render-error]');
		expect(shown?.textContent).toBe(SERVER_SENTENCE);
		expect(view.target.textContent).not.toContain('Request failed');
	});

	it('tells the member why when the very first preview is refused', async () => {
		log.refuseFirst = true;
		const host = {
			tabId: 'tab-1',
			tool: 'image',
			source: SOURCE,
			state: subjectState(),
			activeTool: 'subject',
			run: null,
			phone: false,
			setState: () => undefined,
			setDirty: () => undefined,
			setTitle: () => undefined,
			setThumbnail: () => undefined,
			setSource: () => undefined,
			replaceSource: () => undefined,
			setActiveTool: () => undefined,
			setRun: () => undefined,
			setActions: () => undefined,
			openToolTab: () => 'tab-2'
		} as unknown as ToolTabHost;
		const view = mountComponent(ImageEditorTab, { host });
		await settle();
		expect(log.previews).toBe(1);
		expect(view.target.querySelector('[data-editor-layer]')).toBeNull();
		expect(view.target.querySelector('[data-render-error]')?.textContent).toBe(SERVER_SENTENCE);
		expect(view.target.querySelector('[data-render-error]')?.getAttribute('role')).toBe('alert');
		expect(view.target.textContent).not.toContain('Request failed');
		expect(view.target.querySelector('[data-frame-undo]')).not.toBeNull();
		expect(view.target.querySelector('[data-tool-rail]')).not.toBeNull();
		expect(view.target.querySelector('[data-edit-stack] [data-stack-row]')).not.toBeNull();
	});
});
