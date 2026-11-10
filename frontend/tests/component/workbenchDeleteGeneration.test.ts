import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn().mockResolvedValue({ success: false }),
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
vi.mock('$lib/stores/auth', async (importOriginal) => {
	const { writable } = await import('svelte/store');
	return { ...(await importOriginal<object>()), authStore: writable({ user: { account_type: 'USER' } }) };
});

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { default: Workbench } = await import('$lib/components/Workbench.svelte');
const { createClassComponent } = await import('svelte/legacy');

const IMAGE = '/api/media/generations/gen-1/1.png';
const COMPLETED = {
	currentGeneration: { id: 'gen-1', status: 'completed' },
	workbenchTotal: 1,
	batchImages: [{ url: IMAGE, originalUrl: IMAGE, file_type: 'image' }]
};

let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void; $set: (props: object) => void } | undefined;

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

async function mountWorkbench(props: Record<string, unknown>) {
	component = createClassComponent({
		component: Workbench as never,
		target,
		props: { emptyLayout: 'pane', currentGeneration: null, isGenerating: false, workbenchIndex: 0, workbenchTotal: 0, ...props }
	}) as never;
	await settle(6);
}

const button = (label: string) => target.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);

describe('Delete generation in the Workbench', () => {
	it('sits right after Download and looks exactly like it', async () => {
		await mountWorkbench({ ...COMPLETED, onDeleteGeneration: vi.fn() });
		const download = button('Download file')!;
		const remove = button('Delete generation')!;
		expect(download).not.toBeNull();
		expect(remove).not.toBeNull();
		expect(remove.className).toBe(download.className);
		expect(remove.querySelector('svg')!.getAttribute('class')).toBe(download.querySelector('svg')!.getAttribute('class'));
		const actions = [...download.closest('.flex.gap-2')!.querySelectorAll('button[aria-label]')].map((b) => b.getAttribute('aria-label'));
		expect(actions.indexOf('Delete generation')).toBe(actions.indexOf('Download file') + 1);
		expect(remove.hasAttribute('title')).toBe(false);
	});

	it('asks to delete when clicked', async () => {
		const onDeleteGeneration = vi.fn();
		await mountWorkbench({ ...COMPLETED, onDeleteGeneration });
		button('Delete generation')!.click();
		expect(onDeleteGeneration).toHaveBeenCalledTimes(1);
	});

	it('is absent where nobody handles it', async () => {
		await mountWorkbench({ ...COMPLETED });
		expect(button('Download file')).not.toBeNull();
		expect(button('Delete generation')).toBeNull();
	});

	it('is absent while the generation is still running, like the other result actions', async () => {
		await mountWorkbench({
			currentGeneration: { id: 'gen-1', status: 'running', current_image: '/api/media/previews/gen-1.png' },
			isGenerating: true,
			onDeleteGeneration: vi.fn()
		});
		expect(button('Delete generation')).toBeNull();
	});
});
