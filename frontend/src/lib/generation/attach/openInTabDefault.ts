import { get } from 'svelte/store';
import { goto } from '$app/navigation';
import { api } from '$lib/services/api';
import { getGenerationFailure } from '$lib/services/admin-api';
import { tabsStore } from '$lib/stores/tabs';
import { authStore } from '$lib/stores/auth';
import { toasts } from '$lib/stores/toast';
import { attachExistingGenerations } from '$lib/generation/restore/attachHost';
import { announceReuseConversion } from '$lib/utils/reuseNotice';
import type { GenerationHistoryItem } from '$lib/types/history';
import { openGenerationInTab, type OpenInTabDeps, type OpenInTabResult } from './openInTab';

export function defaultOpenInTabDeps(): OpenInTabDeps {
	return {
		getGeneration: (id) => api.getGenerationById(id, false, true, { includeBatch: true }) as Promise<{ success: boolean; data?: GenerationHistoryItem }>,
		failureHint: async (id) => {
			const response = await getGenerationFailure(id);
			return response.success ? (response.data?.hint ?? null) : null;
		},
		isAdmin: () => get(authStore).user?.account_type === 'ADMIN',
		tabs: {
			current: () => get(tabsStore),
			addTabWithData: (name, data) => tabsStore.addTabWithData(name, data),
			setActiveTab: (tabId) => tabsStore.setActiveTab(tabId),
			updateTab: (tabId, updates) => tabsStore.updateTab(tabId, updates)
		},
		attach: attachExistingGenerations,
		notifyError: (message) => toasts.error(message),
		announceReuse: announceReuseConversion
	};
}

export async function openGenerationInNewTab(
	id: string,
	options: { navigate?: boolean } = {}
): Promise<OpenInTabResult> {
	const result = await openGenerationInTab(id, defaultOpenInTabDeps());
	if (options.navigate && (result.kind === 'opened' || result.kind === 'focused')) {
		if (typeof window === 'undefined' || !window.location.pathname.startsWith('/generate')) void goto('/generate');
	}
	return result;
}
