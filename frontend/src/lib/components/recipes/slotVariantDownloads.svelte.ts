import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';
import {
	initialModelDownloadState,
	reduceModelDownloadState,
	type ModelDownloadEvent,
	type ModelDownloadState
} from '$lib/utils/modelDownloadState';
import type { RecipeSlotVariants } from '$lib/services/api/recipes';

const POLL_MS = 2000;

export class SlotVariantDownloads {
	states = $state<Record<string, ModelDownloadState>>({});
	#timers: Record<string, ReturnType<typeof setTimeout>> = {};
	#disposed = false;
	#onCompleted: () => void;

	constructor(onCompleted: () => void = () => {}) {
		this.#onCompleted = onCompleted;
	}

	stateFor(variantId: string): ModelDownloadState {
		return this.states[variantId] ?? initialModelDownloadState;
	}

	#apply(variantId: string, event: ModelDownloadEvent) {
		this.states = { ...this.states, [variantId]: reduceModelDownloadState(this.stateFor(variantId), event) };
	}

	async start(slot: RecipeSlotVariants, variantId: string) {
		this.#apply(variantId, { type: 'start' });
		try {
			const result = await api.downloadSlotVariant({
				recipe_id: slot.recipe_id,
				artifact_id: slot.id,
				variant_id: variantId
			});
			if (this.#disposed) return;
			this.#apply(variantId, { type: 'started', downloadId: result.download_id });
			this.#poll(variantId, result.download_id);
		} catch (error: any) {
			if (this.#disposed) return;
			if (error?.response?.status === 403) {
				this.#apply(variantId, { type: 'forbidden' });
				return;
			}
			logger.error('[SlotVariantDownloads] Failed to start download:', error);
			const detail = error?.response?.data?.detail;
			this.#apply(variantId, {
				type: 'error',
				message: (typeof detail === 'object' ? detail?.message : detail) || 'Failed to start download'
			});
		}
	}

	#poll(variantId: string, downloadId: string) {
		clearTimeout(this.#timers[variantId]);
		this.#timers[variantId] = setTimeout(async () => {
			if (this.#disposed) return;
			try {
				const response = await api.getModelDownloadStatus(downloadId);
				if (this.#disposed) return;
				if (!response.success || !response.data) {
					this.#poll(variantId, downloadId);
					return;
				}
				const { status, progress, error } = response.data;
				this.#apply(variantId, { type: 'poll', status, progress, error });
				if (status === 'completed') {
					delete this.#timers[variantId];
					this.#onCompleted();
				} else if (status === 'failed') {
					delete this.#timers[variantId];
				} else {
					this.#poll(variantId, downloadId);
				}
			} catch (error) {
				if (this.#disposed) return;
				logger.error('[SlotVariantDownloads] Failed to poll download:', error);
				this.#poll(variantId, downloadId);
			}
		}, POLL_MS);
	}

	dispose() {
		this.#disposed = true;
		Object.values(this.#timers).forEach(clearTimeout);
		this.#timers = {};
	}
}
