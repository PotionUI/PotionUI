import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn(),
		getGenerationParams: vi.fn(),
		updateGenerationTags: vi.fn(),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
	}
}));

vi.mock('$app/stores', () => {
	const { writable } = require('svelte/store');
	return { page: writable({ url: new URL('http://localhost/generate') }) };
});

vi.mock('@google/model-viewer', () => ({}));

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { default: Workbench } = await import('$lib/components/Workbench.svelte');
const { createClassComponent } = await import('svelte/legacy');

const IMAGE = '/api/media/generations/gen-1/1.png';
let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void } | undefined;

beforeEach(() => {
	originalRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
	target = document.createElement('div');
	document.body.appendChild(target);
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target.remove();
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = originalRO;
	vi.clearAllMocks();
});

function mountResult(flags: Record<string, boolean> = {}) {
	component = createClassComponent({
		component: Workbench as never,
		target,
		props: {
			emptyLayout: 'pane',
			currentGeneration: { id: 'gen-1', status: 'completed' },
			isGenerating: false,
			workbenchIndex: 0,
			workbenchTotal: 1,
			batchImages: [{ url: IMAGE, originalUrl: IMAGE, file_type: 'image', ...flags }]
		}
	});
}

function mountPreview(flags: Record<string, boolean> = {}) {
	component = createClassComponent({
		component: Workbench as never,
		target,
		props: {
			emptyLayout: 'pane',
			currentGeneration: { id: 'gen-1', status: 'running', current_image: '/api/media/previews/gen-1.png', ...flags },
			isGenerating: true,
			workbenchIndex: 0,
			workbenchTotal: 0
		}
	});
}

const glow = () => document.querySelectorAll('.ambient:not(.ambient--fill) img');
const echo = () => document.querySelectorAll('.ambient--fill img');
const floating = () => target.querySelector('#workbench-container')!.classList.contains('overflow-visible');

describe('Workbench ambient glow', () => {
	it('glows behind an ordinary result', async () => {
		mountResult();
		await settle(10);
		expect(glow()).toHaveLength(1);
		expect(floating()).toBe(true);
	});

	it('skips the glow and keeps the bordered card while a live preview shows', async () => {
		mountPreview();
		await settle(10);
		expect(glow()).toHaveLength(0);
		expect(floating()).toBe(false);
	});

	it.each([
		['a content-flagged result', () => mountResult({ content_flagged: true })],
		['an NSFW result', () => mountResult({ nsfw: true })],
		['a flagged live preview', () => mountPreview({ content_flagged: true })],
		['a suppressed live preview', () => mountPreview({ preview_suppressed: true })]
	])('never glows from %s and keeps it in the bordered card', async (_name, mountCase) => {
		mountCase();
		await settle(10);
		expect(glow()).toHaveLength(0);
		expect(floating()).toBe(false);
	});

	it('keeps the full-screen echo off an NSFW result', async () => {
		mountResult({ nsfw: true });
		await settle(10);
		target.querySelector<HTMLButtonElement>('button[aria-label="Expand preview"]')!.click();
		await settle(10);
		expect(document.querySelector('[role="dialog"][aria-label="Image preview"]')).not.toBeNull();
		expect(echo()).toHaveLength(0);
	});

	it('echoes an ordinary result behind the full-screen preview', async () => {
		mountResult();
		await settle(10);
		target.querySelector<HTMLButtonElement>('button[aria-label="Expand preview"]')!.click();
		await settle(10);
		expect(echo()).toHaveLength(1);
	});
});
