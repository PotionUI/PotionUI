import { describe, it, expect, vi, beforeEach } from 'vitest';

const owned = new Set<string>();

vi.mock('$lib/generation/compare/compareStore.svelte', () => ({
	ownsGeneration: (id: string | undefined) => !!id && owned.has(id)
}));
vi.mock('$lib/stores/toast', () => ({ toasts: { show: vi.fn() } }));
vi.mock('$lib/utils/notificationChime', () => ({ playNotificationChime: vi.fn() }));

import { toasts } from '$lib/stores/toast';
import { notificationsWebSocket } from './notificationsWebsocket';

function push(type: string, generationId: string) {
	(notificationsWebSocket as unknown as { onMessage: (m: unknown) => void }).onMessage({
		type: 'notification',
		notification: {
			id: `${type}-${generationId}`,
			type,
			level: type === 'generation.failed' ? 'error' : 'success',
			title: 'Generation completed',
			message: '',
			metadata: { generation_id: generationId },
			read: false,
			created_at: new Date().toISOString()
		}
	});
}

beforeEach(() => {
	owned.clear();
	vi.mocked(toasts.show).mockReset();
});

describe('notification toasts for compare cells', () => {
	it('shows the completion toast for an ordinary generation', () => {
		push('generation.completed', 'solo');
		expect(toasts.show).toHaveBeenCalledTimes(1);
	});

	it('suppresses the completion toast for a compare grid cell', () => {
		owned.add('cell-1');
		push('generation.completed', 'cell-1');
		expect(toasts.show).not.toHaveBeenCalled();
	});

	it('still shows a failure toast for a compare grid cell', () => {
		owned.add('cell-2');
		push('generation.failed', 'cell-2');
		expect(toasts.show).toHaveBeenCalledTimes(1);
	});
});
