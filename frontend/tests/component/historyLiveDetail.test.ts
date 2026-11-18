import { afterEach, describe, expect, it, vi } from 'vitest';
import { settle } from './helpers/settle';

const sockets = vi.hoisted(() => {
	const handlers = new Map<string, Set<(m: { type: string; [key: string]: unknown }) => void>>();
	return {
		handlers,
		emit(id: string, message: { type: string; [key: string]: unknown }) {
			handlers.get(id)?.forEach((handler) => handler(message));
		}
	};
});

vi.mock('$lib/generation/live/liveDefault', async () => {
	const { createLiveWatcher } = await import('$lib/generation/live/liveWatch');
	const { readable } = await import('svelte/store');
	return {
		liveWatcher: createLiveWatcher({
			createSocket: () => ({
				connect() {},
				disconnect() {},
				subscribe(id, handler) {
					if (!sockets.handlers.has(id)) sockets.handlers.set(id, new Set());
					sockets.handlers.get(id)!.add(handler);
				},
				unsubscribe(id, handler) {
					if (handler) sockets.handlers.get(id)?.delete(handler);
					else sockets.handlers.delete(id);
				}
			})
		}),
		liveClock: readable(Date.parse('2026-11-18T14:02:26Z'))
	};
});

const api = vi.hoisted(() => ({
	getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
	getClient: vi.fn(() => ({
		get: vi.fn().mockRejectedValue(new Error('not mocked')),
		post: vi.fn().mockRejectedValue(new Error('not mocked')),
		put: vi.fn().mockRejectedValue(new Error('not mocked'))
	})),
	getGenerationThumbnailURL: vi.fn(() => '/thumb.png'),
	getGenerationParams: vi.fn().mockResolvedValue({ success: true, data: { parameters: { prompt: 'A concrete observatory dome at dusk', steps: 28 }, models: [] } }),
	getGenerationById: vi.fn(),
	cancelGeneration: vi.fn().mockResolvedValue({ success: true, data: { cancel_notice: null } }),
	getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
	getBaseURL: vi.fn(() => ''),
	getToken: vi.fn(() => null),
	setOnAuthExpired: vi.fn(),
	setOnTokenRefreshed: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({ api }));
vi.mock('$lib/tool-tabs/continueEdit', () => ({
	continueEditInNewTab: vi.fn(async () => ({ kind: 'opened', tabId: 't1' }))
}));

const { default: GenerationDetailsModal } = await import('$lib/components/modals/GenerationDetailsModal.svelte');
const { default: GenerationCard } = await import('$lib/components/GenerationCard.svelte');
const { liveWatcher } = await import('$lib/generation/live/liveDefault');
const { createClassComponent } = await import('svelte/legacy');

const PREVIEW = 'data:image/png;base64,AAAA';

function record(over: Record<string, unknown> = {}) {
	return {
		id: 'gen-live',
		preset_id: 'krea2',
		preset_name: 'Krea 2',
		mode: 'txt2img',
		form_data: { prompt: 'A concrete observatory dome at dusk', steps: 28, cfg: 3.5, seed: -1, width: 1920, height: 1080, quantity: 4 },
		status: 'running',
		progress: 0.4,
		created_at: '2026-11-18T14:02:00Z',
		started_at: '2026-11-18T14:02:03Z',
		updated_at: '2026-11-18T14:02:20Z',
		files: [],
		segments: [],
		rating: 0,
		is_favorite: false,
		origin: null,
		...over
	};
}

function mountDetails(generation: Record<string, unknown>) {
	const target = document.body.appendChild(document.createElement('div'));
	const component = createClassComponent({
		component: GenerationDetailsModal as never,
		target,
		props: { generation: generation as never, isOpen: true }
	});
	return { destroy: () => component.$destroy() };
}

const q = (selector: string) => document.body.querySelector(selector);

afterEach(() => {
	document.body.innerHTML = '';
	sockets.handlers.clear();
	vi.clearAllMocks();
});

describe('a running generation in Generation Details', () => {
	it('opens with the recorded parameters, a cancel action and disabled result actions', async () => {
		api.getGenerationById.mockResolvedValue({ success: true, data: record() });
		const view = mountDetails(record());
		await settle();
		expect(q('[data-live-stage]')).not.toBeNull();
		expect(q('[data-live-action="cancel"]')).not.toBeNull();
		expect(q('[data-live-action="reuse"]')).not.toBeNull();
		expect(q('[data-live-disabled="download"] button')?.hasAttribute('disabled')).toBe(true);
		expect(document.body.textContent).toContain('A concrete observatory dome at dusk');
		expect(document.body.textContent).not.toContain('Chosen when sampling starts');
		expect(document.body.textContent).not.toContain('Information');
		expect(api.getGenerationParams).toHaveBeenCalledWith('gen-live', 0);
		view.destroy();
	});

	it('subscribes on open and unsubscribes on close', async () => {
		api.getGenerationById.mockResolvedValue({ success: true, data: record() });
		const view = mountDetails(record());
		await settle();
		expect(sockets.handlers.get('gen-live')?.size).toBe(1);
		view.destroy();
		await settle();
		expect(sockets.handlers.has('gen-live')).toBe(false);
	});

	it('shows the step text, the replayed preview and the elapsed clock', async () => {
		api.getGenerationById.mockResolvedValue({ success: true, data: record() });
		const view = mountDetails(record());
		await settle();
		sockets.emit('gen-live', { type: 'generation_status', status: 'running', progress: 0.4, pipe_name: 'generator', current_step: '<<PIPE:generator:bolt>> <<PROGRESS:17/28>>', item_index: 1, item_count: 4 });
		sockets.emit('gen-live', { type: 'workbench_update', image: PREVIEW, replayed: true });
		await settle();
		expect(q('[data-live-step]')?.textContent).toBe('Generating image 2 of 4 · step 17/28');
		expect(q('[data-live-preview]')?.getAttribute('src')).toBe(PREVIEW);
		expect(q('[data-live-elapsed]')?.textContent).toContain('00:23');
		expect(q('[data-live-batch]')).not.toBeNull();
		view.destroy();
	});

	it('renders a preview clip instead of a still when one arrives', async () => {
		api.getGenerationById.mockResolvedValue({ success: true, data: record() });
		const view = mountDetails(record());
		await settle();
		sockets.emit('gen-live', {
			type: 'workbench_preview_clip',
			image: 'data:image/webp;base64,AAAA',
			frames: 4,
			columns: 2,
			frame_width: 64,
			frame_height: 64,
			fps: 8,
			step: 4,
			total_steps: 28,
			source_indices: [0, 1, 2, 3]
		});
		await settle();
		expect(q('[data-preview-clip]')).not.toBeNull();
		expect(document.body.textContent).toContain('Looping preview · low resolution');
		view.destroy();
	});

	it('turns into the finished view in place on completion', async () => {
		api.getGenerationById.mockResolvedValue({ success: true, data: record() });
		const view = mountDetails(record());
		await settle();
		api.getGenerationById.mockResolvedValue({
			success: true,
			data: record({
				status: 'completed',
				progress: 1,
				files: [
					{ id: 1, file_path: '/outputs/gen-live/0.png', file_type: 'image', width: 512, height: 512, is_final: true },
					{ id: 2, file_path: '/outputs/gen-live/1.png', file_type: 'image', width: 512, height: 512, is_final: true }
				]
			})
		});
		sockets.emit('gen-live', { type: 'generation_complete', data: { id: 'gen-live' } });
		await settle();
		await settle();
		expect(q('[data-live-stage]')).toBeNull();
		expect(q('[data-live-finished-notice]')).toBeNull();
		expect(sockets.handlers.has('gen-live')).toBe(false);
		view.destroy();
	});

	it('shows the error where the media was and keeps the parameters', async () => {
		api.getGenerationById.mockResolvedValue({ success: true, data: record() });
		const view = mountDetails(record());
		await settle();
		api.getGenerationById.mockResolvedValue({
			success: true,
			data: record({ status: 'failed', error_message: 'out of memory', error_user_message: 'The GPU ran out of memory.', error_contact_admin: true, error_id: 'e7c2f10a' })
		});
		sockets.emit('gen-live', { type: 'generation_error', message: 'out of memory' });
		await settle();
		await settle();
		expect(q('[data-live-mode="ended"] [data-live-failure]')?.textContent).toContain('The GPU ran out of memory.');
		expect(document.body.textContent).toContain('A concrete observatory dome at dusk');
		expect(q('[data-live-action="reuse"]')?.textContent).toContain('Try again in a tab');
		expect(q('[data-live-action="cancel"]')).toBeNull();
		view.destroy();
	});

	it('cancels through the existing endpoint', async () => {
		api.getGenerationById.mockResolvedValue({ success: true, data: record() });
		const view = mountDetails(record());
		await settle();
		(q('[data-live-action="cancel"]') as HTMLElement).click();
		await settle();
		expect(api.cancelGeneration).toHaveBeenCalledWith('gen-live');
		view.destroy();
	});

	it('offers Open in a tab for MCP, Open the edit for the editor and nothing for chat or a closed tab', async () => {
		const cases: Array<[unknown, string | null]> = [
			[{ kind: 'mcp', key_name: 'k' }, 'open-in-tab'],
			[{ kind: 'editor', edit_id: 'ed_1' }, 'open-edit'],
			[{ kind: 'chat' }, null],
			[null, null]
		];
		for (const [origin, action] of cases) {
			const running = record({ origin });
			api.getGenerationById.mockResolvedValue({ success: true, data: running });
			const view = mountDetails(running);
			await settle();
			const found = ['open-in-tab', 'go-to-tab', 'open-edit'].filter((kind) => q(`[data-live-action="${kind}"]`));
			expect(found).toEqual(action ? [action] : []);
			view.destroy();
			await settle();
		}
	});

	it('shows the origin badge in the header and no Source row', async () => {
		const running = record({ origin: { kind: 'mcp', key_name: 'claude-code' } });
		api.getGenerationById.mockResolvedValue({ success: true, data: running });
		const view = mountDetails(running);
		await settle();
		expect(q('[data-details-origin] [data-origin-chip="mcp"]')?.textContent).toContain('Started over MCP · claude-code');
		expect(document.body.textContent).not.toContain('Source');
		view.destroy();
	});
});

describe('a running tile in History', () => {
	function mountTile(generation: Record<string, unknown>) {
		const target = document.body.appendChild(document.createElement('div'));
		const clicks: unknown[] = [];
		const component = createClassComponent({
			component: GenerationCard as never,
			target,
			props: { generation: generation as never, live: true, tile: { width: 320, height: 200 } }
		});
		component.$on('imageClick', (event: CustomEvent) => clicks.push(event.detail));
		return { target, clicks, destroy: () => component.$destroy() };
	}

	it('streams its preview and progress while mounted and stops on unmount', async () => {
		const tile = mountTile(record());
		await settle();
		expect(sockets.handlers.get('gen-live')?.size).toBe(1);
		sockets.emit('gen-live', { type: 'generation_status', status: 'running', progress: 0.43, pipe_name: 'generator', current_step: '<<PROGRESS:17/28>>' });
		sockets.emit('gen-live', { type: 'workbench_update', image: PREVIEW, replayed: true });
		await settle();
		expect(tile.target.querySelector('[data-live-tile-preview]')?.getAttribute('src')).toBe(PREVIEW);
		expect(tile.target.querySelector('[data-live-tile-chip]')?.textContent).toContain('Generating · 17/28');
		expect(tile.target.querySelector('[data-live-tile-elapsed]')?.textContent).toBe('00:23');
		tile.destroy();
		await settle();
		expect(sockets.handlers.has('gen-live')).toBe(false);
	});

	it('opens a running generation on click', async () => {
		const tile = mountTile(record());
		await settle();
		(tile.target.querySelector('.media-zoom') as HTMLElement).click();
		expect(tile.clicks).toHaveLength(1);
		tile.destroy();
	});

	it('keeps no more tile subscriptions than the bound', async () => {
		const tiles = Array.from({ length: 12 }, (_, i) => mountTile(record({ id: `gen-${i}` })));
		await settle();
		expect(liveWatcher.tileCount()).toBeLessThanOrEqual(8);
		tiles.forEach((tile) => tile.destroy());
	});
});
