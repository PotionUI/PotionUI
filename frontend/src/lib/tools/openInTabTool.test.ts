import { beforeAll, describe, expect, it, vi } from 'vitest';
import { groupToolsFor, type ToolRunHost } from './tools';
import { buildHistoryEntryContext } from './entryMenu';
import { registerEntryTools } from './entryTools';
import type { GenerationHistoryItem } from '$lib/types/history';

vi.mock('$lib/stores/toast', () => ({ toasts: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

function generation(overrides: Partial<GenerationHistoryItem> = {}): GenerationHistoryItem {
	return {
		id: 'g1',
		preset_id: 'p',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [],
		rating: 0,
		is_favorite: false,
		...overrides
	} as GenerationHistoryItem;
}

const openInTab = vi.fn();
const HOST: ToolRunHost = { open: vi.fn(), reuse: vi.fn(), openInTab };

beforeAll(() => registerEntryTools());

function toolIds(item: GenerationHistoryItem, host: ToolRunHost = HOST): string[] {
	const ctx = buildHistoryEntryContext(item, undefined, null);
	return groupToolsFor(ctx, { surface: 'entry', host }).flatMap((group) => group.tools.map(({ tool }) => tool.id));
}

describe('the Open in a tab entry tool', () => {
	it('is offered for a generation started over MCP, finished or not', () => {
		const mcp = { kind: 'mcp', key_id: 'k', key_name: 'n' };
		expect(toolIds(generation({ origin: mcp }))).toContain('open-in-tab');
		expect(toolIds(generation({ origin: mcp, status: 'running' }))).toContain('open-in-tab');
		expect(toolIds(generation({ origin: mcp, status: 'failed' }))).toContain('open-in-tab');
	});

	it('is never offered for chat, tab-started or editor generations, running or not', () => {
		const editor = { kind: 'editor', edit_id: 'e1', task: 'edit_region' };
		for (const status of ['completed', 'running', 'failed'] as const) {
			expect(toolIds(generation({ origin: { kind: 'chat' }, status }))).not.toContain('open-in-tab');
			expect(toolIds(generation({ origin: null, status }))).not.toContain('open-in-tab');
			expect(toolIds(generation({ origin: editor, status, mode: 'editor:edit_region' }))).not.toContain('open-in-tab');
		}
	});

	it('is left out where the page cannot open tabs', () => {
		const { openInTab: _omitted, ...without } = HOST;
		expect(toolIds(generation({ origin: { kind: 'mcp', key_id: 'k' } }), without)).not.toContain('open-in-tab');
	});

	it('runs through the host', () => {
		const item = generation({ origin: { kind: 'mcp', key_id: 'k' } });
		const ctx = buildHistoryEntryContext(item, undefined, null);
		const tool = groupToolsFor(ctx, { surface: 'entry', host: HOST })
			.flatMap((group) => group.tools)
			.find(({ tool }) => tool.id === 'open-in-tab')!.tool;
		tool.run!(ctx, HOST);
		expect(openInTab).toHaveBeenCalledWith(ctx);
	});
});
