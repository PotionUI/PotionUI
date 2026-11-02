import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';
import { apply, beginEdit, createStack, type EditStep } from '$lib/tool-tabs/frame/editStack';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import { cropDraft, lookDraft } from '$lib/tool-tabs/image/steps';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const log = vi.hoisted(() => ({ renders: [] as string[][], resets: 0 }));

vi.mock('$lib/media/editors/loadImage', () => ({
	loadImage: async () => ({ naturalWidth: 640, naturalHeight: 480 })
}));

vi.mock('$lib/tool-tabs/image/render', async (original) => {
	const actual = await original<typeof import('$lib/tool-tabs/image/render')>();
	class RecordingRenderer {
		constructor(private source: { size: { width: number; height: number } }) {}
		setSource(source: { size: { width: number; height: number } }) {
			this.source = source;
		}
		reset() {
			log.resets += 1;
		}
		get ref() {
			return null;
		}
		async render(steps: readonly EditStep[]) {
			log.renders.push(steps.filter((step) => !step.hidden).map((step) => step.tool));
			const canvas = document.createElement('canvas');
			canvas.width = 480;
			canvas.height = 480;
			return { canvas, size: { width: 480, height: 480 } };
		}
		async payload() {
			return [];
		}
		async bake() {
			return 'art';
		}
	}
	return { ...actual, ImageRenderer: RecordingRenderer };
});

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

const SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	relative_path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	url: '/api/media/generations/gen-1/observatory-dusk.png',
	name: 'observatory-dusk.png',
	type: 'image',
	generation_file_id: '501'
};

function cropAndLook() {
	let stack = apply(
		beginEdit(
			createStack(),
			cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0.125, y: 0, width: 0.75, height: 1 } }, aspect: '1:1' }, '1:1', { width: 480, height: 480 })
		)
	);
	stack = apply(beginEdit(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 })));
	return writeImageState({ ...emptyImageState('resize'), stack });
}

function host(): ToolTabHost {
	return {
		tabId: 'tab-1',
		tool: 'image',
		source: SOURCE,
		state: cropAndLook(),
		activeTool: 'resize',
		run: null,
		hint: null,
		phone: false,
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	};
}

async function settle() {
	for (let round = 0; round < 4; round++) {
		for (let i = 0; i < 8; i++) await Promise.resolve();
		vi.advanceTimersByTime(100);
		flushSync();
	}
}

describe('ImageEditorTab after the browser drops its drawing surface', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		log.renders = [];
		log.resets = 0;
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('renders the stack again from the source when the display canvas is restored', async () => {
		const view = mountComponent(ImageEditorTab, { host: host() });
		await settle();
		const display = view.target.querySelector<HTMLCanvasElement>('[data-editor-layer]');
		expect(display).not.toBeNull();
		expect(log.renders).toEqual([['crop', 'effects']]);
		expect(log.resets).toBe(0);

		display!.dispatchEvent(new Event('contextlost'));
		await settle();
		expect(log.renders).toHaveLength(1);

		display!.dispatchEvent(new Event('contextrestored'));
		await settle();
		expect(log.resets).toBe(1);
		expect(log.renders).toEqual([
			['crop', 'effects'],
			['crop', 'effects']
		]);
		expect(view.target.querySelector('[data-editor-layer]')).not.toBeNull();
		view.destroy();
	});
});
