import { reconcileTabGenerations, type ReconcileApi, type ReconcileTabsStore } from './reconcile';

export interface ChatGenerationDeps {
	api: ReconcileApi;
	tabsStore: ReconcileTabsStore;
	subscriptionOwner: unknown;
	onSubscribe: (generationId: string) => void;
	unsubscribe: (generationId: string) => void;
	signal?: AbortSignal;
}

export function attachChatStartedGeneration(
	result: Record<string, unknown>,
	deps: ChatGenerationDeps
): Promise<void> | void {
	const generationId = typeof result.generation_id === 'string' ? result.generation_id : null;
	const tabId = typeof result.tab_id === 'string' ? result.tab_id : null;
	if (!generationId || !tabId) return;
	return reconcileTabGenerations(tabId, deps.api, deps.tabsStore, {
		extraCandidateIds: [generationId],
		signal: deps.signal,
		subscriptionOwner: deps.subscriptionOwner,
		onSubscribe: deps.onSubscribe,
		unsubscribe: deps.unsubscribe
	});
}
