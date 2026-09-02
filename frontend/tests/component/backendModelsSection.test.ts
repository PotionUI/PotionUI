// @vitest-environment jsdom
//
// The Backends -> Remote -> Models tab lists every model file known on the
// worker's host, filtered by search/type/status, with push/fetch actions on
// the selection. Mounts the real section against a fixture of six rows
// spanning the three sync statuses and two model types, proving the default
// view (on_worker), each filter, Clear filters, and select-all -> push all
// carry the id set the toolbar actually shows.
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { RemoteModelSyncRow } from '$lib/services/admin-api';

vi.mock('$lib/services/admin-api', () => ({
	getRemoteModelSyncView: vi.fn(),
	pushRemoteModels: vi.fn(),
	fetchRemoteModels: vi.fn(),
	getRemoteModelTransfers: vi.fn()
}));

const adminApi = await import('$lib/services/admin-api');
const { default: BackendModelsSection } = await import(
	'../../src/routes/admin/components/BackendModelsSection.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function row(overrides: Partial<RemoteModelSyncRow> = {}): RemoteModelSyncRow {
	return {
		model_id: 'm',
		filename: 'model.safetensors',
		model_type: 'checkpoints',
		size_bytes: 100,
		status: 'on_worker',
		providers_can_fetch: true,
		...overrides
	};
}

const ROWS: RemoteModelSyncRow[] = [
	row({ model_id: 'm1', filename: 'checkpoint-a.safetensors', model_type: 'checkpoints', status: 'on_worker' }),
	row({ model_id: 'm2', filename: 'checkpoint-b.safetensors', model_type: 'checkpoints', status: 'on_worker' }),
	row({ model_id: 'm3', filename: 'lora-a.safetensors', model_type: 'loras', status: 'missing' }),
	row({ model_id: 'm4', filename: 'lora-b.safetensors', model_type: 'loras', status: 'missing' }),
	row({ model_id: 'm5', filename: 'checkpoint-c.safetensors', model_type: 'checkpoints', status: 'digest_mismatch' }),
	row({ model_id: 'm6', filename: 'lora-c.safetensors', model_type: 'loras', status: 'on_worker' })
];

function mountSection(props: { backendId?: string; onOpenInfrastructure?: () => void } = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: BackendModelsSection as never,
		target,
		props: { backendId: 'backend-1', ...props }
	});
	return {
		target,
		rows: () => Array.from(target.querySelectorAll<HTMLElement>('[role="option"]')),
		filename: (i: number) => target.querySelectorAll<HTMLElement>('[role="option"]')[i]?.textContent ?? '',
		button: (text: string) =>
			Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes(text)),
		searchInput: () => target.querySelector<HTMLInputElement>('input[aria-label="Search by filename"]'),
		typeSelect: () => target.querySelector<HTMLSelectElement>('select[aria-label="Filter by model type"]'),
		selectAll: () => target.querySelector<HTMLInputElement>('input[aria-label="Select all filtered models"]'),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountSection> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('BackendModelsSection', () => {
	it('defaults to showing only on_worker rows', async () => {
		vi.mocked(adminApi.getRemoteModelSyncView).mockResolvedValue({ success: true, data: { models: ROWS } });
		vi.mocked(adminApi.getRemoteModelTransfers).mockResolvedValue({ success: true, data: { transfers: [] } });

		mounted = mountSection();
		await settle();

		const filenames = mounted.rows().map((r) => r.textContent);
		expect(filenames.some((t) => t?.includes('checkpoint-a'))).toBe(true);
		expect(filenames.some((t) => t?.includes('checkpoint-b'))).toBe(true);
		expect(filenames.some((t) => t?.includes('lora-c'))).toBe(true);
		expect(mounted.rows()).toHaveLength(3);
	});

	it('narrows by filename as the admin types', async () => {
		vi.mocked(adminApi.getRemoteModelSyncView).mockResolvedValue({ success: true, data: { models: ROWS } });
		vi.mocked(adminApi.getRemoteModelTransfers).mockResolvedValue({ success: true, data: { transfers: [] } });

		mounted = mountSection();
		await settle();

		const input = mounted.searchInput();
		expect(input).toBeTruthy();
		input!.value = 'lora-c';
		input!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(mounted.rows()).toHaveLength(1);
		expect(mounted.filename(0)).toContain('lora-c');
	});

	it('narrows by model type when a type is chosen', async () => {
		vi.mocked(adminApi.getRemoteModelSyncView).mockResolvedValue({ success: true, data: { models: ROWS } });
		vi.mocked(adminApi.getRemoteModelTransfers).mockResolvedValue({ success: true, data: { transfers: [] } });

		mounted = mountSection();
		await settle();

		const select = mounted.typeSelect();
		expect(select).toBeTruthy();
		select!.value = 'checkpoints';
		select!.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();

		const filenames = mounted.rows().map((r) => r.textContent);
		expect(mounted.rows()).toHaveLength(2);
		expect(filenames.every((t) => t?.includes('checkpoint'))).toBe(true);
	});

	it('shows only missing rows behind the Missing pill, with correct pill counts', async () => {
		vi.mocked(adminApi.getRemoteModelSyncView).mockResolvedValue({ success: true, data: { models: ROWS } });
		vi.mocked(adminApi.getRemoteModelTransfers).mockResolvedValue({ success: true, data: { transfers: [] } });

		mounted = mountSection();
		await settle();

		mounted.button('Missing')?.click();
		await settle();

		const filenames = mounted.rows().map((r) => r.textContent);
		expect(mounted.rows()).toHaveLength(2);
		expect(filenames.every((t) => t?.includes('lora-a') || t?.includes('lora-b'))).toBe(true);

		expect(mounted.button('On worker')?.textContent).toContain('3');
		expect(mounted.button('Missing')?.textContent).toContain('2');
		expect(mounted.button('Mismatch')?.textContent).toContain('1');
		expect(mounted.button('All')?.textContent).toContain('6');
	});

	it('returns to the on_worker default on Clear filters', async () => {
		vi.mocked(adminApi.getRemoteModelSyncView).mockResolvedValue({ success: true, data: { models: ROWS } });
		vi.mocked(adminApi.getRemoteModelTransfers).mockResolvedValue({ success: true, data: { transfers: [] } });

		mounted = mountSection();
		await settle();

		const input = mounted.searchInput();
		input!.value = 'lora';
		input!.dispatchEvent(new Event('input', { bubbles: true }));
		mounted.button('All')?.click();
		await settle();
		expect(mounted.searchInput()?.value).toBe('lora');
		expect(mounted.button('Clear filters')).toBeTruthy();

		mounted.button('Clear filters')?.click();
		await settle();

		expect(mounted.searchInput()?.value).toBe('');
		expect(mounted.rows()).toHaveLength(3);
		const filenames = mounted.rows().map((r) => r.textContent);
		expect(filenames.some((t) => t?.includes('checkpoint-a'))).toBe(true);
		expect(filenames.some((t) => t?.includes('checkpoint-b'))).toBe(true);
		expect(filenames.some((t) => t?.includes('lora-c'))).toBe(true);
	});

	it('selects all filtered rows and pushes exactly that id set', async () => {
		vi.mocked(adminApi.getRemoteModelSyncView).mockResolvedValue({ success: true, data: { models: ROWS } });
		vi.mocked(adminApi.getRemoteModelTransfers).mockResolvedValue({ success: true, data: { transfers: [] } });
		vi.mocked(adminApi.pushRemoteModels).mockResolvedValue({ success: true, data: { transfers: [] } });

		mounted = mountSection();
		await settle();

		mounted.selectAll()?.click();
		await settle();
		mounted.button('Upload from this machine')?.click();
		await settle();

		expect(adminApi.pushRemoteModels).toHaveBeenCalledWith('backend-1', ['m1', 'm2', 'm6']);
	});

	it('shows the not-running empty state with Retry and Open Infrastructure', async () => {
		vi.mocked(adminApi.getRemoteModelSyncView).mockResolvedValue({
			success: false,
			error: 'worker_not_running',
			message: 'The worker is stopped or still starting'
		});
		const onOpenInfrastructure = vi.fn();

		mounted = mountSection({ onOpenInfrastructure });
		await settle();

		expect(mounted.target.textContent).toContain("Worker isn't running");
		expect(mounted.button('Retry')).toBeTruthy();

		mounted.button('Open Infrastructure')?.click();

		expect(onOpenInfrastructure).toHaveBeenCalledOnce();
	});

	it('omits Open Infrastructure when the caller gives no handler', async () => {
		vi.mocked(adminApi.getRemoteModelSyncView).mockResolvedValue({
			success: false,
			error: 'worker_not_running',
			message: 'The worker is stopped or still starting'
		});

		mounted = mountSection();
		await settle();

		expect(mounted.button('Open Infrastructure')).toBeFalsy();
	});
});
