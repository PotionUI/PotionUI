// @vitest-environment jsdom
//
// Render smoke test for the admin Logs panel: renders lines from the tail
// endpoint, re-fetches with the selected level, and shows the two empty
// states (file logging off, no lines yet).
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { LogTail } from '$lib/services/admin-api';

vi.mock('$lib/services/admin-api', () => ({
	getLogTail: vi.fn()
}));

const adminApi = await import('$lib/services/admin-api');
const { default: LogsPanel } = await import('../../src/routes/admin/components/settings/LogsPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

function tail(overrides: Partial<LogTail> = {}): LogTail {
	return {
		lines: [
			{ ts: '2026-09-09 10:00:00', level: 'INFO', logger: 'is', message: 'hello' },
			{ ts: '2026-09-09 10:00:01', level: 'ERROR', logger: 'is.generation', message: 'boom' }
		],
		truncated: false,
		file: '/srv/potionui/storage/logs/potionui.log',
		size_bytes: 2048,
		...overrides
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mountPanel() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: LogsPanel as never, target, props: {} });
	return {
		target,
		component,
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
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('LogsPanel', () => {
	it('renders the lines from the tail response', async () => {
		vi.mocked(adminApi.getLogTail).mockResolvedValue({ success: true, data: tail() });

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('hello');
		expect(mounted.target.textContent).toContain('boom');
		expect(mounted.target.textContent).toContain('INFO');
		expect(mounted.target.textContent).toContain('ERROR');
		expect(adminApi.getLogTail).toHaveBeenCalledWith(500, 'INFO');
	});

	it('re-fetches with the selected level when the level control changes', async () => {
		vi.mocked(adminApi.getLogTail).mockResolvedValue({ success: true, data: tail() });

		mounted = mountPanel();
		await settle();

		const warningButton = Array.from(mounted.target.querySelectorAll('nav button')).find((b) =>
			b.textContent?.includes('Warning')
		) as HTMLButtonElement | undefined;
		expect(warningButton).toBeTruthy();

		vi.mocked(adminApi.getLogTail).mockClear();
		vi.mocked(adminApi.getLogTail).mockResolvedValue({
			success: true,
			data: tail({ lines: [{ ts: '2026-09-09 10:00:01', level: 'ERROR', logger: 'is', message: 'boom' }] })
		});
		warningButton!.click();
		await settle();

		expect(adminApi.getLogTail).toHaveBeenCalledWith(500, 'WARNING');
	});

	it('shows the file-logging-off empty state', async () => {
		vi.mocked(adminApi.getLogTail).mockResolvedValue({
			success: true,
			data: tail({ file: null, size_bytes: 0, lines: [] })
		});

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('File logging is off');
	});

	it('shows the no-lines-yet empty state for an existing but empty file', async () => {
		vi.mocked(adminApi.getLogTail).mockResolvedValue({
			success: true,
			data: tail({ lines: [], size_bytes: 0 })
		});

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('No log lines yet');
	});
});
