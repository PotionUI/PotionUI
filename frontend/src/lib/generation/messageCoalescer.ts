/**
 * Minimal shape a message must have to be coalesced: `type` decides the
 * coalescing rule, `generation_id` scopes last-wins/dedup to one generation
 * so concurrent generations (different tabs, or a queued one behind the
 * active one) never interfere with each other's buffered frame.
 */
export interface CoalescableMessage {
	type: string;
	generation_id?: string;
	[key: string]: unknown;
}

/**
 * Message types the backend emits once per sampling step with no
 * throttling — a rapid, superseding snapshot where only the newest one
 * queued in a frame is worth applying.
 */
const COALESCED_TYPES = new Set(['generation_status', 'workbench_update', 'timer_update']);

/**
 * A terminal event ends a generation — its buffer must drain immediately so
 * a subscriber reading `generation_complete`/`generation_error`/
 * `generation_cancelled` never races a still-buffered status for the same
 * generation (status is always flushed before the terminal message that
 * follows it, since both are drained from the same FIFO queue).
 */
const TERMINAL_TYPES = new Set(['generation_complete', 'generation_error', 'generation_cancelled']);

/**
 * Fields excluded when comparing a `generation_status` message to the last
 * one applied for its generation -- none exist on today's wire payload
 * (`src/features/generation/handlers/artifact_handlers.py::serialize_progress_output`
 * sets only `status`/`current_step`/`message`/`progress` plus the
 * occasionally-present `current_step_num`/`total_steps`/`segment_id`/
 * `pipe_id`/`pipe_name`, none of which change on their own between
 * ticks), but a field added later that legitimately changes every message
 * regardless of visible state (a per-message timestamp, say) would defeat
 * dedup entirely unless it's named here.
 */
const STATUS_DEDUP_EXCLUDED_FIELDS: ReadonlySet<string> = new Set();

/** Oldest-entries-evicted-first cap on `done`/`lastApplied` bookkeeping so a
 *  long session doesn't grow these forever, one entry per generation the
 *  page has ever seen finish. */
const MAX_TRACKED_GENERATIONS = 256;

function defaultScheduleFlush(flush: () => void): void {
	if (typeof requestAnimationFrame === 'function') {
		requestAnimationFrame(flush);
	} else {
		setTimeout(flush, 16);
	}
}

/** Deep-equal via a sorted-key JSON string, so two messages with the same
 *  fields in a different insertion order still compare equal. */
function stableStringify(value: unknown, excludeKeys: ReadonlySet<string>): string {
	if (value === null || typeof value !== 'object') return JSON.stringify(value);
	if (Array.isArray(value)) return `[${value.map((entry) => stableStringify(entry, excludeKeys)).join(',')}]`;
	const keys = Object.keys(value as Record<string, unknown>)
		.filter((key) => !excludeKeys.has(key))
		.sort();
	return `{${keys
		.map((key) => `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key], excludeKeys)}`)
		.join(',')}}`;
}

/**
 * Buffers incoming messages and applies them in one batch per animation
 * frame, so a subscriber reacting to every message (tabsStore, in
 * particular) re-renders once per frame instead of once per sampling step.
 * Framework-agnostic: `dispatch` is the only side effect, and the flush
 * scheduler is injectable for tests.
 */
export class MessageCoalescer<M extends CoalescableMessage> {
	private queue: M[] = [];
	private coalescedSlot = new Map<string, number>();
	private lastApplied = new Map<string, M>();
	private done = new Set<string>();
	private flushScheduled = false;

	constructor(
		private readonly dispatch: (message: M) => void,
		private readonly scheduleFlush: (flush: () => void) => void = defaultScheduleFlush
	) {}

	enqueue(message: M): void {
		const generationId = message.generation_id;
		const isCoalesced = COALESCED_TYPES.has(message.type);
		const isTerminal = TERMINAL_TYPES.has(message.type);

		if (isCoalesced && generationId) {
			// A terminal event already flushed and closed this generation --
			// never let a late/reordered status reopen fields (currentProgress,
			// timers, the live preview) the terminal handler just cleared.
			if (this.done.has(generationId)) return;
			if (message.type === 'generation_status' && this.isDuplicateStatus(generationId, message)) return;

			const key = `${message.type}:${generationId}`;
			const slot = this.coalescedSlot.get(key);
			if (slot !== undefined) {
				this.queue[slot] = message;
			} else {
				this.coalescedSlot.set(key, this.queue.length);
				this.queue.push(message);
			}
		} else {
			this.queue.push(message);
		}

		if (isTerminal) {
			if (generationId) this.markDone(generationId);
			this.flush();
			return;
		}

		if (!this.flushScheduled) {
			this.flushScheduled = true;
			this.scheduleFlush(() => this.flush());
		}
	}

	private isDuplicateStatus(generationId: string, message: M): boolean {
		const last = this.lastApplied.get(generationId);
		if (!last) return false;
		return (
			stableStringify(message, STATUS_DEDUP_EXCLUDED_FIELDS) === stableStringify(last, STATUS_DEDUP_EXCLUDED_FIELDS)
		);
	}

	/** Closes a generation out: no further status/workbench/timer message for
	 *  it will ever be applied (see the `done` check in `enqueue`), so its
	 *  cached last-applied status is dropped too -- keeping it around would
	 *  only let a stale value wrongly dedup a genuinely new one if `done`
	 *  ever evicts this id (below) and the id is somehow reused. */
	private markDone(generationId: string): void {
		this.done.add(generationId);
		this.lastApplied.delete(generationId);
		if (this.done.size > MAX_TRACKED_GENERATIONS) {
			const oldest = this.done.values().next().value;
			if (oldest !== undefined) this.done.delete(oldest);
		}
	}

	private flush(): void {
		this.flushScheduled = false;
		if (this.queue.length === 0) return;

		const pending = this.queue;
		this.queue = [];
		this.coalescedSlot.clear();

		for (const message of pending) {
			if (message.type === 'generation_status' && message.generation_id) {
				this.lastApplied.set(message.generation_id, message);
			}
			this.dispatch(message);
		}
	}
}
