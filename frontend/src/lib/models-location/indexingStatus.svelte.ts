import { api } from '$lib/services/api';
import { logger } from '$lib/utils/logger';
import type { IndexingStatus } from '$lib/services/api/models';
import { indexingIsRunning } from './indexingDisplay';

const POLL_INTERVAL_MS = 2000;

class IndexingStatusStore {
	status = $state<IndexingStatus | null>(null);
	loading = $state(false);
	error = $state<string | null>(null);

	private subscribers = 0;
	private timer: ReturnType<typeof setInterval> | null = null;
	private inFlight: Promise<void> | null = null;

	subscribe(): () => void {
		this.subscribers += 1;
		if (this.subscribers === 1) {
			void this.refresh();
		} else if (indexingIsRunning(this.status)) {
			this.ensurePolling();
		}
		return () => {
			this.subscribers = Math.max(0, this.subscribers - 1);
			if (this.subscribers === 0) this.stopPolling();
		};
	}

	async refresh(): Promise<void> {
		if (this.inFlight) return this.inFlight;
		this.loading = this.status === null;
		this.inFlight = (async () => {
			try {
				const response = await api.getIndexingStatus();
				if (response.success && response.data) {
					this.applyStatus(response.data);
				} else {
					this.error = response.message ?? 'Failed to load indexing status.';
				}
			} catch (e) {
				logger.error('Failed to load indexing status:', e);
				this.error = 'Failed to load indexing status.';
			} finally {
				this.loading = false;
				this.inFlight = null;
			}
		})();
		return this.inFlight;
	}

	notifyRunStarted(status: IndexingStatus): void {
		this.applyStatus(status);
	}

	private applyStatus(status: IndexingStatus): void {
		this.status = status;
		this.error = null;
		if (this.subscribers > 0 && indexingIsRunning(status)) {
			this.ensurePolling();
		} else {
			this.stopPolling();
		}
	}

	private ensurePolling(): void {
		if (this.timer !== null) return;
		this.timer = setInterval(() => void this.refresh(), POLL_INTERVAL_MS);
	}

	private stopPolling(): void {
		if (this.timer !== null) {
			clearInterval(this.timer);
			this.timer = null;
		}
	}
}

export const indexingStatusStore = new IndexingStatusStore();
