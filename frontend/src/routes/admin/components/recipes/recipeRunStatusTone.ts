import type { SetupRunStatus } from '$lib/services/api/setup';

export type RunStatusTone = 'success' | 'danger' | 'warning' | 'info' | 'signal' | 'muted';

export function runStatusTone(status: SetupRunStatus): RunStatusTone {
	switch (status) {
		case 'completed':
			return 'success';
		case 'failed':
			return 'danger';
		case 'paused':
			return 'warning';
		case 'awaiting_consent':
			return 'signal';
		case 'pending':
		case 'running':
			return 'info';
		default:
			return 'muted';
	}
}
