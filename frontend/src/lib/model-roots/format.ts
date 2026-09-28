import type { ModelRootState } from '$lib/services/api/models';

export const ROOT_STATE_BADGE: Record<ModelRootState, { variant: 'success' | 'danger' | 'warning'; label: string }> = {
	online: { variant: 'success', label: 'Online' },
	offline: { variant: 'danger', label: 'Offline' },
	unreadable: { variant: 'warning', label: 'Unreadable' }
};
