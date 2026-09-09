// @vitest-environment jsdom
//
// Render smoke test for the admin Housekeeping panel: loads the dedicated
// overview endpoint, renders the three retention previews and the last-run
// summary from the response, writes edits through onSettingChange (the three
// keys ride the shared System Settings save bar, this panel has no save of
// its own), and triggers a manual run via the dedicated run endpoint.
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { HousekeepingOverview, HousekeepingRun } from '$lib/services/admin-api';

vi.mock('$lib/services/admin-api', () => ({
	getHousekeeping: vi.fn(),
	runHousekeeping: vi.fn()
}));

const adminApi = await import('$lib/services/admin-api');
const { default: HousekeepingPanel } = await import('../../src/routes/admin/components/settings/HousekeepingPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

function run(overrides: Partial<HousekeepingRun> = {}): HousekeepingRun {
	return {
		started_at: '2026-09-09T10:00:00+00:00',
		finished_at: '2026-09-09T10:00:04+00:00',
		tasks: {
			tmp: { removed: 1204, bytes_freed: 7730941132, errors: [] },
			run_reports: { rows_removed: 310, errors: [] },
			llm_traces: { rows_removed: 42, errors: [] }
		},
		errors: [],
		...overrides
	};
}

function overview(overrides: Partial<HousekeepingOverview> = {}): HousekeepingOverview {
	return {
		settings: { tmp_retention_days: 7, run_report_retention_days: 30, llm_trace_retention_days: 7 },
		running: false,
		last_run: run(),
		next_run_at: '2026-09-10T10:00:00+00:00',
		preview: { tmp: { files: 1204, bytes: 7730941132 }, run_reports: 310, llm_traces: 42 },
		...overrides
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mountPanel(settings: Record<string, unknown> = {}, savedSnapshot = '{}') {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const onSettingChange = vi.fn();
	const component = createClassComponent({
		component: HousekeepingPanel as never,
		target,
		props: { settings, onSettingChange, savedSnapshot }
	});
	return {
		target,
		component,
		onSettingChange,
		input: (id: string) => target.querySelector<HTMLInputElement>(`#${id}`),
		button: (text: string) =>
			Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes(text)),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountPanel> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('HousekeepingPanel', () => {
	it('renders the three previews and the last-run line from the overview response', async () => {
		vi.mocked(adminApi.getHousekeeping).mockResolvedValue({ success: true, data: overview() });

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('would remove 7.2 GB · 1,204 files');
		expect(mounted.target.textContent).toContain('would remove 310 rows');
		expect(mounted.target.textContent).toContain('would remove 42 rows');
		expect(mounted.target.textContent).toContain('freed 7.2 GB');
		expect(mounted.target.textContent).toContain('352 rows');
		expect(mounted.target.textContent).not.toContain('Never run');
	});

	it('shows nothing-to-remove and Never run when the overview has no history', async () => {
		vi.mocked(adminApi.getHousekeeping).mockResolvedValue({
			success: true,
			data: overview({ last_run: null, preview: { tmp: { files: 0, bytes: 0 }, run_reports: 0, llm_traces: 0 } })
		});

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('Never run');
		expect(mounted.target.textContent).toContain('nothing to remove');
	});

	it('writes a scratch-files edit through onSettingChange', async () => {
		vi.mocked(adminApi.getHousekeeping).mockResolvedValue({ success: true, data: overview() });

		mounted = mountPanel({ tmp_retention_days: 7 });
		await settle();

		const input = mounted.input('housekeeping-tmp-retention');
		expect(input).toBeTruthy();
		input!.value = '14';
		input!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(mounted.onSettingChange).toHaveBeenCalledWith('tmp_retention_days', 14);
	});

	it('runs housekeeping now via the dedicated endpoint', async () => {
		vi.mocked(adminApi.getHousekeeping).mockResolvedValue({ success: true, data: overview() });
		vi.mocked(adminApi.runHousekeeping).mockResolvedValue({ success: true, data: run() });

		mounted = mountPanel();
		await settle();

		expect(adminApi.runHousekeeping).not.toHaveBeenCalled();
		mounted.button('Run now')?.click();
		await settle();

		expect(adminApi.runHousekeeping).toHaveBeenCalledTimes(1);
	});
});
