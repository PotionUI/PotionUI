import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/stores/toast', () => ({ toasts: { show: vi.fn() } }));
vi.mock('$lib/stores/tabDraftSync', () => ({ tabDraftSync: { receiveDraft: vi.fn() } }));
vi.mock('$lib/utils/notificationChime', () => ({ playNotificationChime: vi.fn() }));
vi.mock('$lib/stores/sessionLiveSync', () => ({ sessionLiveSync: { receive: vi.fn() } }));

import { toasts } from '$lib/stores/toast';
import { tabDraftSync } from '$lib/stores/tabDraftSync';
import { sessionLiveSync } from '$lib/stores/sessionLiveSync';
import { get } from 'svelte/store';
import { elixirChanged } from '$lib/elixirs/events';
import { notificationsWebSocket } from './notificationsWebsocket';

function push(type: string, metadata: Record<string, unknown>, title = 'Generation completed', message = '') {
	(notificationsWebSocket as unknown as { onMessage: (m: unknown) => void }).onMessage({
		type: 'notification',
		notification: {
			id: `${type}-${JSON.stringify(metadata)}`,
			type,
			level: type === 'generation.failed' ? 'error' : 'success',
			title,
			message,
			metadata,
			read: false,
			created_at: new Date().toISOString()
		}
	});
}

beforeEach(() => {
	vi.mocked(toasts.show).mockReset();
});

describe('generation notification toasts', () => {
	it('shows the completion toast for an ordinary generation', () => {
		push('generation.completed', { generation_id: 'solo' });
		expect(toasts.show).toHaveBeenCalledTimes(1);
	});

	it('shows the single grid completion toast with its cell summary', () => {
		push('generation.completed', { grid_id: 'grid-1' }, 'Comparison finished', '12 of 12 cells done.');
		expect(toasts.show).toHaveBeenCalledTimes(1);
		expect(vi.mocked(toasts.show).mock.calls[0][1]).toBe('12 of 12 cells done.');
		expect(vi.mocked(toasts.show).mock.calls[0][2]).toMatchObject({ title: 'Comparison finished' });
	});

	it('shows a failure toast for a compare grid cell', () => {
		push('generation.failed', { generation_id: 'cell-2' });
		expect(toasts.show).toHaveBeenCalledTimes(1);
	});
});

describe('tab_variables_changed push', () => {
	it('routes the draft into the tab draft sync engine', () => {
		const doc = { variables: { mood: { type: 'text', value: 'noir' } } };
		(notificationsWebSocket as unknown as { onMessage: (m: unknown) => void }).onMessage({
			type: 'tab_variables_changed',
			tab_id: 't1',
			doc,
			rev: 5
		});
		expect(tabDraftSync.receiveDraft).toHaveBeenCalledWith('t1', doc, 5);
		expect(toasts.show).not.toHaveBeenCalled();
	});
});

describe('session_changed push', () => {
	it('hands the change to the live session sync without a toast of its own', () => {
		const message = {
			type: 'session_changed',
			session_id: 's1',
			version: 4,
			origin: { kind: 'mcp', label: 'Claude Code' },
			client_id: null
		};
		(notificationsWebSocket as unknown as { onMessage: (m: unknown) => void }).onMessage(message);
		expect(sessionLiveSync.receive).toHaveBeenCalledWith(message);
		expect(toasts.show).not.toHaveBeenCalled();
	});
});

describe('elixir_changed push', () => {
	it('tells the open elixir what changed', () => {
		(notificationsWebSocket as unknown as { onMessage: (m: unknown) => void }).onMessage({
			type: 'elixir_changed',
			elixir_id: 'x1',
			revision: 7,
			run_id: 'r1'
		});
		expect(get(elixirChanged)).toEqual({ elixir_id: 'x1', revision: 7, run_id: 'r1' });
		expect(toasts.show).not.toHaveBeenCalled();
	});
});
