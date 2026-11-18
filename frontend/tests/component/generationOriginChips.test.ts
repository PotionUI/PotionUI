import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountComponent } from './helpers/mount';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			post: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationThumbnailURL: vi.fn(() => '/thumb.png'),
		getGenerationParams: vi.fn().mockResolvedValue({ success: true, data: { parameters: {}, models: [] } }),
		getGenerationById: vi.fn().mockResolvedValue({ success: false, error: 'not mocked' }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn()
	}
}));

vi.mock('$lib/tool-tabs/continueEdit', () => ({
	continueEditInNewTab: vi.fn(async () => ({ kind: 'opened', tabId: 't1' }))
}));

const { default: GenerationOriginChip } = await import('$lib/components/GenerationOriginChip.svelte');
const { default: GenerationCard } = await import('$lib/components/GenerationCard.svelte');
const { default: GenerationDetailsModal } = await import('$lib/components/modals/GenerationDetailsModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

const MCP = { kind: 'mcp', key_id: 'k1', key_name: 'old name', key_current_name: 'claude desktop' };
const EDITOR = { kind: 'editor', edit_id: 'ed_1', step_id: 's1', task: 'edit_region', run_id: 'er_1' };
const CHAT = { kind: 'chat' };

function generation(origin: unknown, status = 'failed') {
	return {
		id: 'gen-1',
		form_data: {},
		status,
		progress: 0,
		created_at: '2026-10-05T10:00:00Z',
		updated_at: '2026-10-05T10:00:00Z',
		files: [],
		segments: [],
		rating: 0,
		is_favorite: false,
		origin
	};
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('the origin chip', () => {
	it('says which key an MCP generation was started with and is not a link', async () => {
		const view = mountComponent(GenerationOriginChip, { generation: generation(MCP) });
		const chip = view.target.querySelector('[data-origin-chip="mcp"]')!;
		expect(chip.textContent?.trim()).toBe('Started over MCP · claude desktop');
		expect(view.target.querySelector('button, a')).toBeNull();
		view.destroy();
	});

	it('says a chat generation was started from chat and is not a link', async () => {
		const view = mountComponent(GenerationOriginChip, { generation: generation(CHAT) });
		expect(view.target.querySelector('[data-origin-chip="chat"]')!.textContent?.trim()).toBe('Started from chat');
		expect(view.target.querySelector('button, a')).toBeNull();
		view.destroy();
	});

	it('keeps the editor chip opening the edit', async () => {
		const view = mountComponent(GenerationOriginChip, { generation: generation(EDITOR, 'completed') });
		const chip = view.target.querySelector<HTMLButtonElement>('[data-editor-origin="open"]')!;
		expect(chip.textContent?.trim()).toBe('Made in the editor');
		chip.click();
		await settle();
		const { continueEditInNewTab } = await import('$lib/tool-tabs/continueEdit');
		expect(continueEditInNewTab).toHaveBeenCalledWith('ed_1', null);
		view.destroy();
	});

	it('draws nothing for a generation started in a tab', async () => {
		for (const origin of [null, undefined, { kind: 'tab' }]) {
			const view = mountComponent(GenerationOriginChip, { generation: generation(origin) });
			expect(view.target.textContent?.trim()).toBe('');
			expect(view.target.querySelector('[data-origin-chip], [data-editor-origin]')).toBeNull();
			view.destroy();
		}
	});

	it('keeps its words for the screen reader when drawn as an icon', async () => {
		const view = mountComponent(GenerationOriginChip, { generation: generation(MCP), iconOnly: true });
		const chip = view.target.querySelector('[data-origin-chip="mcp"]')!;
		expect(chip.getAttribute('aria-label')).toBe('Started over MCP · claude desktop');
		expect(chip.textContent?.trim()).toBe('');
		view.destroy();
	});
});

describe('the History card', () => {
	function mountCard(origin: unknown, status = 'failed') {
		const target = document.body.appendChild(document.createElement('div'));
		const component = createClassComponent({ component: GenerationCard as never, target, props: { generation: generation(origin, status) as never } });
		return { target, destroy: () => component.$destroy() };
	}

	it.each([
		['mcp', MCP, 'Started over MCP · claude desktop'],
		['chat', CHAT, 'Started from chat']
	])('shows where a %s generation came from, whatever its state', (kind, origin, label) => {
		const card = mountCard(origin);
		expect(card.target.querySelector(`[data-origin-chip="${kind}"]`)!.textContent?.trim()).toBe(label);
		card.destroy();
	});

	it('shows the editor chip for a finished editor generation only', () => {
		const done = mountCard(EDITOR, 'completed');
		expect(done.target.querySelector('[data-editor-origin="open"]')).not.toBeNull();
		done.destroy();
		const failed = mountCard(EDITOR, 'failed');
		expect(failed.target.querySelector('[data-editor-origin], [data-origin-chip]')).toBeNull();
		failed.destroy();
	});

	it('shows nothing for a generation started in a tab', () => {
		const card = mountCard(null);
		expect(card.target.querySelector('[data-origin-chip], [data-editor-origin]')).toBeNull();
		card.destroy();
	});
});

describe('the generation details', () => {
	function mountDetails(origin: unknown, status = 'completed') {
		const target = document.body.appendChild(document.createElement('div'));
		const component = createClassComponent({
			component: GenerationDetailsModal as never,
			target,
			props: { generation: { ...generation(origin, status), files: [{ file_path: '/outputs/gen-1/0.png', file_type: 'image', width: 512, height: 512 }] } as never, isOpen: true }
		});
		return { destroy: () => component.$destroy() };
	}

	it.each([
		['mcp', MCP, 'Started over MCP · claude desktop'],
		['chat', CHAT, 'Started from chat']
	])('names the %s origin', (kind, origin, label) => {
		const details = mountDetails(origin);
		const row = document.body.querySelector('[data-details-origin]')!;
		expect(row.querySelector(`[data-origin-chip="${kind}"]`)!.textContent?.trim()).toBe(label);
		details.destroy();
	});

	it('has no origin badge for a generation started in a tab', () => {
		const details = mountDetails(null);
		expect(document.body.querySelector('[data-details-origin]')).toBeNull();
		details.destroy();
	});

	it('offers Open in a tab for a generation started over MCP only', () => {
		const mcp = mountDetails(MCP);
		expect(document.body.textContent).toContain('Open in a tab');
		mcp.destroy();
		for (const [origin, status] of [[CHAT, 'completed'], [EDITOR, 'completed'], [null, 'running'], [null, 'completed']] as const) {
			const other = mountDetails(origin, status);
			expect(document.body.textContent).not.toContain('Open in a tab');
			other.destroy();
		}
	});
});
