import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	createHistoryVersionWatcher,
	HISTORY_CHANGE_DEBOUNCE_MS,
	HISTORY_RECOVERY_INTERVAL_MS,
	HISTORY_VERSION_INTERVAL_MS,
	HISTORY_VERSION_JITTER,
	type HistoryVersionWatcher
} from './historyVersionWatcher';

// Just under the recovery interval, so this window measures the token poll
// alone; the recovery refresh has its own test.
const IDLE_MS = 59_000;

// A page of history at the default 24 items per page, close enough in shape to
// the real payload to make the size comparison meaningful.
function fullHistoryPage() {
	return {
		success: true,
		data: {
			total: 240,
			generations: Array.from({ length: 24 }, (_, index) => ({
				id: `01J8Z${String(index).padStart(21, 'X')}`,
				preset_id: 'native/SDXL/realistic',
				preset_name: 'SDXL Realistic',
				status: 'completed',
				progress: 1,
				mode: 'txt2img',
				rating: 0,
				is_favorite: false,
				created_at: '2026-09-05 11:20:33',
				completed_at: '2026-09-05 11:21:02',
				duration_ms: 29000,
				parameters: { prompt: 'a fox in the snow', negative_prompt: 'blurry', seed: 12345 },
				tags: [{ id: 'tag-1', name: 'keepers', color: '#888888' }],
				files: [
					{
						id: `file-${index}-0`,
						file_path: `outputs/2026-09-05/generation-${index}-0.png`,
						file_type: 'IMAGE',
						file_size: 1843200,
						is_final: true,
						nsfw: false
					}
				]
			}))
		}
	};
}

function versionPayload(version: string) {
	return { success: true, data: { version } };
}

describe('stores/historyVersionWatcher', () => {
	let fetchVersion: ReturnType<typeof vi.fn>;
	let reload: ReturnType<typeof vi.fn>;
	let visible: boolean;
	let page: number;
	let watcher: HistoryVersionWatcher;

	function build(overrides: Record<string, unknown> = {}) {
		return createHistoryVersionWatcher({
			fetchVersion: () => fetchVersion(),
			reload,
			isVisible: () => visible,
			canDiscover: () => page === 1,
			// Fixed mid-band jitter keeps the cadence deterministic; the jitter
			// itself is covered separately.
			random: () => 0.5,
			...overrides
		});
	}

	// Fake timers plus real promises: every timer advance is followed by a flush
	// so the awaited version request settles before the next assertion.
	async function advance(ms: number) {
		await vi.advanceTimersByTimeAsync(ms);
	}

	beforeEach(() => {
		vi.useFakeTimers();
		fetchVersion = vi.fn().mockResolvedValue('token-1');
		reload = vi.fn();
		visible = true;
		page = 1;
	});

	afterEach(() => {
		watcher?.stop();
		vi.useRealTimers();
		vi.restoreAllMocks();
	});

	it('polls the token on an idle visible page and never reloads while it is stable', async () => {
		watcher = build();
		watcher.start();
		await advance(IDLE_MS);

		// One baseline at start plus one per interval across the idle minute: 8
		// token requests where the 6-second poll made 10 full page requests.
		expect(fetchVersion).toHaveBeenCalledTimes(
			1 + Math.floor(IDLE_MS / HISTORY_VERSION_INTERVAL_MS)
		);
		expect(fetchVersion).toHaveBeenCalledTimes(8);
		expect(reload).not.toHaveBeenCalled();
	});

	it('sends far less over the wire than the page reload it replaces', () => {
		const tokenBytes = JSON.stringify(versionPayload('9f2c1e5b7a8d4306')).length;
		const pageBytes = JSON.stringify(fullHistoryPage()).length;

		expect(tokenBytes * 50).toBeLessThan(pageBytes);
	});

	it('reloads exactly once when the token moves', async () => {
		watcher = build();
		watcher.start();
		await advance(HISTORY_VERSION_INTERVAL_MS);

		fetchVersion.mockResolvedValue('token-2');
		await advance(HISTORY_VERSION_INTERVAL_MS);

		expect(reload).toHaveBeenCalledTimes(1);

		// The new token is the baseline now, so a stable poll adds nothing.
		await advance(HISTORY_VERSION_INTERVAL_MS * 3);
		expect(reload).toHaveBeenCalledTimes(1);
	});

	it('collapses a burst of change notifications into one refetch', async () => {
		watcher = build();
		watcher.start();
		await advance(HISTORY_VERSION_INTERVAL_MS / 2);
		fetchVersion.mockClear();

		for (let i = 0; i < 5; i += 1) watcher.notifyChanged();
		await advance(HISTORY_CHANGE_DEBOUNCE_MS);

		expect(reload).toHaveBeenCalledTimes(1);
		expect(fetchVersion).toHaveBeenCalledTimes(1);
	});

	it('rebaselines on a notified change so the next poll does not reload again', async () => {
		watcher = build();
		watcher.start();

		fetchVersion.mockResolvedValue('token-2');
		watcher.notifyChanged();
		await advance(HISTORY_CHANGE_DEBOUNCE_MS);
		expect(reload).toHaveBeenCalledTimes(1);

		await advance(HISTORY_VERSION_INTERVAL_MS * 3);
		expect(reload).toHaveBeenCalledTimes(1);
	});

	it('issues nothing while the tab is hidden and one check when it returns', async () => {
		watcher = build();
		watcher.start();
		await advance(HISTORY_VERSION_INTERVAL_MS);
		fetchVersion.mockClear();

		visible = false;
		watcher.setVisible(false);
		await advance(IDLE_MS);
		expect(fetchVersion).not.toHaveBeenCalled();

		visible = true;
		watcher.setVisible(true);
		await Promise.resolve();
		expect(fetchVersion).toHaveBeenCalledTimes(1);
	});

	it('checks once on reconnect but not on the first connection', async () => {
		watcher = build();
		watcher.start();
		await advance(1);
		fetchVersion.mockClear();

		watcher.connectionChanged(true);
		await advance(1);
		expect(fetchVersion).not.toHaveBeenCalled();

		watcher.connectionChanged(false);
		watcher.connectionChanged(true);
		await advance(1);
		expect(fetchVersion).toHaveBeenCalledTimes(1);
	});

	it('coalesces overlapping checks onto one request', async () => {
		let settle: (value: string) => void = () => {};
		fetchVersion.mockImplementation(
			() => new Promise<string>((resolve) => (settle = resolve))
		);

		watcher = build();
		watcher.start();
		void watcher.check();
		void watcher.check();

		expect(fetchVersion).toHaveBeenCalledTimes(1);
		settle('token-1');
		await advance(1);
	});

	it('issues nothing past the first page', async () => {
		page = 2;
		watcher = build();
		watcher.start();
		await advance(IDLE_MS);

		expect(fetchVersion).not.toHaveBeenCalled();
		expect(reload).not.toHaveBeenCalled();
	});

	it('does not reload after returning to the first page', async () => {
		watcher = build();
		watcher.start();
		await advance(HISTORY_VERSION_INTERVAL_MS);

		page = 2;
		fetchVersion.mockResolvedValue('token-2');
		await advance(HISTORY_VERSION_INTERVAL_MS * 2);

		page = 1;
		await advance(HISTORY_VERSION_INTERVAL_MS);

		expect(reload).not.toHaveBeenCalled();
	});

	it('treats a failed request as no information, not as a change', async () => {
		watcher = build();
		watcher.start();
		await advance(HISTORY_VERSION_INTERVAL_MS);

		fetchVersion.mockRejectedValue(new Error('offline'));
		await advance(HISTORY_VERSION_INTERVAL_MS * 2);
		expect(reload).not.toHaveBeenCalled();

		fetchVersion.mockResolvedValue('token-1');
		await advance(HISTORY_VERSION_INTERVAL_MS);
		expect(reload).not.toHaveBeenCalled();
	});

	it('runs one recovery refresh per recovery interval even when the token never moves', async () => {
		watcher = build();
		watcher.start();

		await advance(HISTORY_RECOVERY_INTERVAL_MS);
		expect(reload).toHaveBeenCalledTimes(1);

		await advance(HISTORY_RECOVERY_INTERVAL_MS);
		expect(reload).toHaveBeenCalledTimes(2);
	});

	it('holds the recovery refresh while hidden and off the first page', async () => {
		watcher = build();
		watcher.start();

		visible = false;
		watcher.setVisible(false);
		await advance(HISTORY_RECOVERY_INTERVAL_MS * 2);
		expect(reload).not.toHaveBeenCalled();

		visible = true;
		page = 2;
		watcher.setVisible(true);
		await advance(HISTORY_RECOVERY_INTERVAL_MS * 2);
		expect(reload).not.toHaveBeenCalled();
	});

	// A second tab favourites a generation, adds a tag, or moves it between
	// collections. No WebSocket event reaches this tab; only the token moves.
	it('reloads once per metadata change made in another tab', async () => {
		watcher = build();
		watcher.start();
		await advance(HISTORY_VERSION_INTERVAL_MS);
		expect(reload).not.toHaveBeenCalled();

		for (const [index, token] of ['r2', 'r3', 'r4'].entries()) {
			fetchVersion.mockResolvedValue(token);
			await advance(HISTORY_VERSION_INTERVAL_MS);
			expect(reload).toHaveBeenCalledTimes(index + 1);

			// The same token on the next poll must not reload a second time.
			await advance(HISTORY_VERSION_INTERVAL_MS);
			expect(reload).toHaveBeenCalledTimes(index + 1);
		}
	});

	describe('eligibility changing during an outstanding request', () => {
		let settle: (value: string) => void;

		function blockedFetch() {
			fetchVersion.mockImplementation(
				() => new Promise<string>((resolve) => (settle = resolve))
			);
		}

		it('does not reload when the tab hides while a check is in flight', async () => {
			watcher = build();
			watcher.start();
			await advance(1);

			blockedFetch();
			void watcher.check();
			visible = false;
			watcher.setVisible(false);
			settle('token-2');
			await advance(1);

			expect(reload).not.toHaveBeenCalled();
		});

		it('finds the change it withheld once the tab comes back', async () => {
			watcher = build();
			watcher.start();
			await advance(1);

			blockedFetch();
			void watcher.check();
			visible = false;
			watcher.setVisible(false);
			settle('token-2');
			await advance(1);
			expect(reload).not.toHaveBeenCalled();

			// The baseline was left alone, so the same token now reads as a change.
			fetchVersion.mockResolvedValue('token-2');
			visible = true;
			watcher.setVisible(true);
			await advance(1);

			expect(reload).toHaveBeenCalledTimes(1);
		});

		it('does not reload when the viewer pages away while a check is in flight', async () => {
			watcher = build();
			watcher.start();
			await advance(1);

			blockedFetch();
			void watcher.check();
			page = 2;
			settle('token-2');
			await advance(1);

			expect(reload).not.toHaveBeenCalled();
		});

		it('does not reload when the tab hides while a refresh is in flight', async () => {
			watcher = build();
			watcher.start();
			await advance(1);

			blockedFetch();
			watcher.notifyChanged();
			await advance(HISTORY_CHANGE_DEBOUNCE_MS);
			visible = false;
			watcher.setVisible(false);
			settle('token-2');
			await advance(1);

			expect(reload).not.toHaveBeenCalled();
		});

		it('flushes the withheld completion on return to visible', async () => {
			watcher = build();
			watcher.start();
			await advance(1);

			blockedFetch();
			watcher.notifyChanged();
			await advance(HISTORY_CHANGE_DEBOUNCE_MS);
			visible = false;
			watcher.setVisible(false);
			settle('token-2');
			await advance(1);
			expect(reload).not.toHaveBeenCalled();

			fetchVersion.mockResolvedValue('token-2');
			visible = true;
			watcher.setVisible(true);
			await advance(1);

			expect(reload).toHaveBeenCalledTimes(1);
		});
	});

	it('issues nothing when a change is notified while hidden, and flushes it on return', async () => {
		watcher = build();
		watcher.start();
		await advance(1);

		visible = false;
		watcher.setVisible(false);
		fetchVersion.mockClear();
		watcher.notifyChanged();
		await advance(HISTORY_CHANGE_DEBOUNCE_MS * 4);

		expect(fetchVersion).not.toHaveBeenCalled();
		expect(reload).not.toHaveBeenCalled();

		visible = true;
		watcher.setVisible(true);
		await advance(1);

		expect(reload).toHaveBeenCalledTimes(1);
	});

	// Hiding mid-debounce and returning before it would have fired: the timer
	// has to be taken over by the pending-change flag, or the flush and the
	// surviving timer both reload.
	it('reloads once when the tab hides and returns mid-debounce', async () => {
		watcher = build();
		watcher.start();
		await advance(1);

		fetchVersion.mockResolvedValue('token-2');
		watcher.notifyChanged();
		await advance(HISTORY_CHANGE_DEBOUNCE_MS / 4);

		visible = false;
		watcher.setVisible(false);
		await advance(HISTORY_CHANGE_DEBOUNCE_MS / 4);
		expect(reload).not.toHaveBeenCalled();

		visible = true;
		watcher.setVisible(true);
		await advance(HISTORY_CHANGE_DEBOUNCE_MS * 4);

		expect(reload).toHaveBeenCalledTimes(1);
	});

	// Discovery is page-1 only; a terminal event for a generation already on
	// screen is not, matching the reload this watcher replaced.
	it('refreshes a terminal event on a later page but never discovers there', async () => {
		page = 2;
		watcher = build();
		watcher.start();

		fetchVersion.mockResolvedValue('token-2');
		await advance(HISTORY_VERSION_INTERVAL_MS * 3);
		expect(fetchVersion).not.toHaveBeenCalled();
		expect(reload).not.toHaveBeenCalled();

		watcher.notifyChanged();
		await advance(HISTORY_CHANGE_DEBOUNCE_MS);

		expect(reload).toHaveBeenCalledTimes(1);
	});

	it('stops every timer on stop', async () => {
		watcher = build();
		watcher.start();
		await advance(HISTORY_VERSION_INTERVAL_MS);

		watcher.notifyChanged();
		watcher.stop();
		fetchVersion.mockClear();

		await advance(HISTORY_RECOVERY_INTERVAL_MS * 2);
		expect(fetchVersion).not.toHaveBeenCalled();
		expect(reload).not.toHaveBeenCalled();
	});

	it('spreads the interval across the jitter band', async () => {
		for (const [roll, expected] of [
			[0, HISTORY_VERSION_INTERVAL_MS * (1 - HISTORY_VERSION_JITTER)],
			[1, HISTORY_VERSION_INTERVAL_MS * (1 + HISTORY_VERSION_JITTER)]
		] as const) {
			fetchVersion.mockClear();
			const jittered = build({ random: () => roll });
			jittered.start();

			await advance(expected - 1);
			const beforeDeadline = fetchVersion.mock.calls.length;
			await advance(1);

			// The baseline at start is the only call before the first tick lands.
			expect(beforeDeadline).toBe(1);
			expect(fetchVersion).toHaveBeenCalledTimes(2);
			jittered.stop();
		}
	});
});
