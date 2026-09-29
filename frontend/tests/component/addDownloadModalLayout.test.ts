// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { remoteBackends } from '../../src/lib/stores/downloads';

const getModelTypes = vi.fn();
const queueModelDownload = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModelTypes: (...args: unknown[]) => getModelTypes(...args),
		getProviders: async () => ({ success: true, data: [] }),
		getTags: async () => ({ success: true, data: { tags: [] } }),
		searchTags: async () => ({ success: true, data: { tags: [] } })
	}
}));

vi.mock('$lib/stores/downloads', async (importOriginal) => {
	const actual = await importOriginal<typeof import('../../src/lib/stores/downloads')>();
	return {
		...actual,
		downloadStore: {
			...actual.downloadStore,
			queueModelDownload: (...args: unknown[]) => queueModelDownload(...args)
		}
	};
});

const { default: AddDownloadModal } = await import('../../src/routes/admin/components/AddDownloadModal.svelte');

const TYPES = [
	{ type: 'controlnet', directory: '/m/controlnet', count: 0, subdirectories: [] },
	{ type: 'lora', directory: '/m/loras', count: 831, subdirectories: ['sdxl'] },
	{ type: 'checkpoint', directory: '/m/checkpoints', count: 12, subdirectories: [] },
	{ type: 'diffusion_model', directory: '/m/diffusion', count: 71, subdirectories: [] },
	{ type: 'vae', directory: '/m/vae', count: 0, subdirectories: [] }
];

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: ReturnType<typeof mount> | null = null;

async function open() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(AddDownloadModal, { target });
	await settle();
}

function button(text: string): HTMLButtonElement | undefined {
	return [...document.querySelectorAll('button')].find((b) => b.textContent?.trim().startsWith(text)) as HTMLButtonElement | undefined;
}

beforeEach(() => {
	getModelTypes.mockResolvedValue({ success: true, data: { types: TYPES } });
	queueModelDownload.mockResolvedValue({ id: 'dl' });
	remoteBackends.set([]);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	remoteBackends.set([]);
	vi.clearAllMocks();
});

describe('AddDownloadModal layout', () => {
	it('shows no destination control with a single backend', async () => {
		await open();
		expect(document.querySelector('[aria-label="Destination"]')).toBeNull();
		expect(button('This machine')).toBeUndefined();
	});

	it('shows a destination toggle and reveals the remote picker on Remote', async () => {
		remoteBackends.set([
			{ id: 'r1', name: 'Rack A' },
			{ id: 'r2', name: 'Rack B' }
		] as never);
		await open();
		expect(document.querySelector('[aria-label="Destination"]')).not.toBeNull();
		expect(document.getElementById('remote-backend')).toBeNull();
		button('Remote')!.click();
		flushSync();
		const picker = document.getElementById('remote-backend') as HTMLSelectElement;
		expect(picker).not.toBeNull();
		expect(picker.value).toBe('r1');
		picker.value = 'r2';
		picker.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();
		const url = document.getElementById('url') as HTMLInputElement;
		url.value = 'https://example.com/m.safetensors';
		url.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		button('Queue download')!.click();
		await settle();
		expect(queueModelDownload.mock.calls[0][1].destination_backend_id).toBe('r2');
		button('This machine')!.click();
		flushSync();
		expect(document.getElementById('remote-backend')).toBeNull();
	});

	it('renders model types as full-width chips with checkpoint preselected', async () => {
		await open();
		const group = document.querySelector('[role="group"][aria-label="Model type"]')!;
		const chips = [...group.querySelectorAll('button')];
		expect(chips.map((c) => c.textContent!.replace(/\s+/g, ' ').trim())).toEqual([
			'controlnet 0',
			'lora 831',
			'checkpoint 12',
			'diffusion_model 71',
			'vae 0'
		]);
		expect(chips[2].getAttribute('aria-pressed')).toBe('true');
	});
});
