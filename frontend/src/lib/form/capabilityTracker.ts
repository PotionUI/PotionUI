import type { CloudCapabilities } from './capabilityBinder';

export const FAILED_FETCH_RETRY_MS = 5000;
export const CAPABILITY_TTL_MS = 30000;

export class CapabilityCache {
	private readonly entries = new Map<string, { caps: CloudCapabilities; at: number }>();

	constructor(
		private readonly ttlMs: number = CAPABILITY_TTL_MS,
		private readonly now: () => number = Date.now
	) {}

	get(modelId: string): CloudCapabilities | undefined {
		return this.entries.get(modelId)?.caps;
	}

	has(modelId: string): boolean {
		return this.entries.has(modelId);
	}

	isFresh(modelId: string): boolean {
		const entry = this.entries.get(modelId);
		return !!entry && this.now() - entry.at < this.ttlMs;
	}

	set(modelId: string, caps: CloudCapabilities): void {
		this.entries.set(modelId, { caps, at: this.now() });
	}

	expireAll(): void {
		for (const entry of this.entries.values()) entry.at = Number.NEGATIVE_INFINITY;
	}

	clear(): void {
		this.entries.clear();
	}

	keys(): string[] {
		return [...this.entries.keys()];
	}
}

export interface CapabilityTrackerOptions {
	fetch: (modelId: string) => Promise<CloudCapabilities>;
	onLoaded: (modelId: string, caps: CloudCapabilities) => void;
	cache?: CapabilityCache;
	now?: () => number;
}

export interface CapabilityTracker {
	select(modelField: string, modelId: string | null): void;
	reset(): void;
	destroy(): void;
}

export const sharedCapabilityCache = new CapabilityCache();

export function createCapabilityTracker(options: CapabilityTrackerOptions): CapabilityTracker {
	const cache = options.cache ?? new CapabilityCache();
	const now = options.now ?? Date.now;
	const wanted = new Map<string, string | null>();
	const inFlight = new Set<string>();
	const failedAt = new Map<string, number>();
	let dead = false;

	function request(modelId: string): boolean {
		if (cache.isFresh(modelId) || inFlight.has(modelId)) return true;
		const failed = failedAt.get(modelId);
		if (failed !== undefined && now() - failed < FAILED_FETCH_RETRY_MS) return false;
		inFlight.add(modelId);
		options
			.fetch(modelId)
			.then((caps) => {
				failedAt.delete(modelId);
				cache.set(modelId, caps);
				if (!dead) options.onLoaded(modelId, caps);
			})
			.catch(() => {
				failedAt.set(modelId, now());
				for (const [field, id] of wanted) if (id === modelId) wanted.delete(field);
			})
			.finally(() => {
				inFlight.delete(modelId);
			});
		return true;
	}

	return {
		select(modelField, modelId) {
			if (dead) return;
			const unchanged = wanted.has(modelField) && wanted.get(modelField) === modelId;
			if (unchanged && (!modelId || cache.isFresh(modelId))) return;
			wanted.set(modelField, modelId);
			if (modelId && !request(modelId)) wanted.delete(modelField);
		},
		reset() {
			wanted.clear();
			failedAt.clear();
		},
		destroy() {
			dead = true;
		}
	};
}
