// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AdminChatSessionSummary } from '$lib/services/admin-api';

const mockGetAdminChatSessions = vi.fn();
const mockGetAdminChatSessionDetail = vi.fn();
const mockClearChatCallTraces = vi.fn();
const mockClearAdminChatSessions = vi.fn();
const mockAdminBulkDeleteChatSessions = vi.fn();

vi.mock('$lib/services/admin-api', () => ({
	getAdminChatSessions: (...args: unknown[]) => mockGetAdminChatSessions(...args),
	getAdminChatSessionDetail: (...args: unknown[]) => mockGetAdminChatSessionDetail(...args),
	clearChatCallTraces: (...args: unknown[]) => mockClearChatCallTraces(...args),
	clearAdminChatSessions: (...args: unknown[]) => mockClearAdminChatSessions(...args),
	adminBulkDeleteChatSessions: (...args: unknown[]) => mockAdminBulkDeleteChatSessions(...args)
}));

vi.mock('$lib/stores/confirm', () => ({
	confirmDialog: vi.fn(async () => true)
}));

const { default: SessionsDebugTab } = await import('../../src/routes/admin/components/SessionsDebugTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function session(overrides: Partial<AdminChatSessionSummary> = {}): AdminChatSessionSummary {
	return {
		id: 'sess-1',
		user_id: 'user-1',
		mode: 'chat',
		name: 'A conversation',
		status: 'active',
		llm_config_id: 'cfg-1',
		created_at: '2026-09-24T10:00:00+00:00',
		updated_at: '2026-09-24T10:05:00+00:00',
		username: 'someuser',
		email: 'someuser@example.com',
		message_count: 3,
		...overrides
	};
}

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: SessionsDebugTab as never, target, props: {} });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function stubList(sessions: AdminChatSessionSummary[]) {
	mockGetAdminChatSessions.mockResolvedValue({
		success: true,
		data: { sessions, total: sessions.length, limit: 20, offset: 0, tracing_enabled: true }
	});
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('SessionsDebugTab — list and detail', () => {
	it('renders each session as a row and opens its real detail on click', async () => {
		stubList([session(), session({ id: 'sess-2', name: 'Second one' })]);
		mockGetAdminChatSessionDetail.mockResolvedValue({
			success: true,
			data: {
				session: { ...session(), original_text: null, title_generated: false, closed_at: null, metadata: null, messages: [
					{ id: 'msg-1', session_id: 'sess-1', role: 'user', content: 'hello', parsed_content: null, created_at: '2026-09-24T10:00:00+00:00', tokens_used: null, prompt_tokens: null, completion_tokens: null, tool_executions: null, metadata: null }
				] },
				traces: []
			}
		});

		mounted = mount();
		await settle();

		const rows = mounted.target.querySelectorAll('.dt-scroll > [role="row"]');
		expect(rows).toHaveLength(2);
		expect(mounted.target.textContent).toContain('Second one');

		(rows[0] as HTMLElement).click();
		await settle();

		expect(mockGetAdminChatSessionDetail).toHaveBeenCalledWith('sess-1');
		expect(mounted.target.textContent).toContain('hello');
	});
});

describe('SessionsDebugTab — bulk actions', () => {
	it('clears traces for every selected session, one call each', async () => {
		stubList([session(), session({ id: 'sess-2', name: 'Second one' })]);
		mockClearChatCallTraces.mockResolvedValue({ success: true, data: { deleted: 1 } });

		mounted = mount();
		await settle();

		const checkboxes = mounted.target.querySelectorAll('[role="checkbox"][aria-label="Select row"]');
		expect(checkboxes).toHaveLength(2);
		(checkboxes[0] as HTMLElement).click();
		(checkboxes[1] as HTMLElement).click();
		await settle();

		const clearButton = Array.from(mounted.target.querySelectorAll('button')).find(
			(b) => b.textContent?.trim() === 'Clear traces'
		) as HTMLButtonElement;
		expect(clearButton).toBeTruthy();
		clearButton.click();
		await settle();

		expect(mockClearChatCallTraces).toHaveBeenCalledTimes(2);
		expect(mockClearChatCallTraces).toHaveBeenCalledWith('sess-1');
		expect(mockClearChatCallTraces).toHaveBeenCalledWith('sess-2');
	});

	it('deletes every selected session in one admin bulk call', async () => {
		stubList([session(), session({ id: 'sess-2', name: 'Second one' })]);
		mockAdminBulkDeleteChatSessions.mockResolvedValue({ success: true, data: { deleted_count: 2, failed_count: 0, failed_ids: [] } });
		stubList([session(), session({ id: 'sess-2', name: 'Second one' })]);

		mounted = mount();
		await settle();

		const checkboxes = mounted.target.querySelectorAll('[role="checkbox"][aria-label="Select row"]');
		(checkboxes[0] as HTMLElement).click();
		(checkboxes[1] as HTMLElement).click();
		await settle();

		const deleteButton = Array.from(mounted.target.querySelectorAll('button')).find(
			(b) => b.textContent?.trim() === 'Delete'
		) as HTMLButtonElement;
		expect(deleteButton).toBeTruthy();
		deleteButton.click();
		await settle();

		expect(mockAdminBulkDeleteChatSessions).toHaveBeenCalledTimes(1);
		expect(mockAdminBulkDeleteChatSessions).toHaveBeenCalledWith(['sess-1', 'sess-2']);
	});
});
