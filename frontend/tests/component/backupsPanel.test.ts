// @vitest-environment jsdom
//
// Render smoke test for the admin Backups panel: loads the dedicated overview
// endpoint, renders the archive list, the last-backup line and the cron line
// from the response, writes destination and retention edits through
// onSettingChange (the three keys ride the shared System Settings save bar,
// this panel has no save of its own), starts a run with the SAVED default
// tier rather than the dirty form value, and deletes an archive through the
// confirm modal.
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { BackupArchive, BackupOverview } from '$lib/services/admin-api';

vi.mock('$lib/services/admin-api', () => ({
	getBackups: vi.fn(),
	runBackup: vi.fn(),
	getBackupJob: vi.fn(),
	deleteBackup: vi.fn()
}));

const adminApi = await import('$lib/services/admin-api');
const { default: BackupsPanel } = await import('../../src/routes/admin/components/settings/BackupsPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

function archive(overrides: Partial<BackupArchive> = {}): BackupArchive {
	return {
		name: 'potionui-20260909-config.tar.zst',
		path: '/srv/backups/potionui-20260909-config.tar.zst',
		bytes: 7730941132,
		created_at: '2026-09-09T10:00:00+00:00',
		tier: 'config',
		tiers: ['config'],
		app_version: '0.0.5',
		migration_head: 'a1b2c3',
		readable: true,
		...overrides
	};
}

function overview(overrides: Partial<BackupOverview> = {}): BackupOverview {
	return {
		settings: { destination: 'backups', retention: 7, default_tier: 'config' },
		destination_abs: '/srv/potionui/backups',
		exists: true,
		writable: true,
		archives: [archive(), archive({ name: 'potionui-20260908-media.tar.zst', bytes: 1024, tier: 'media', tiers: ['config', 'media'] })],
		mirror: { last_synced: '2026-09-09T10:05:00+00:00', day_count: 3, bytes: 2048 },
		last_backup: { time: '2026-09-09T10:00:00+00:00', bytes: 7730941132, tier: 'config' },
		cron_line: '0 3 * * * /srv/potionui/potionui backup --tier config',
		job: null,
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
		component: BackupsPanel as never,
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
		byLabel: (label: string) =>
			Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.getAttribute('aria-label') === label),
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

describe('BackupsPanel', () => {
	it('renders the archives, the last-backup line and the cron line from the overview response', async () => {
		vi.mocked(adminApi.getBackups).mockResolvedValue({ success: true, data: overview() });

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('potionui-20260909-config.tar.zst');
		expect(mounted.target.textContent).toContain('potionui-20260908-media.tar.zst');
		expect(mounted.target.textContent).toContain('7.2 GB');
		expect(mounted.target.textContent).toContain('1 KB');
		expect(mounted.target.textContent).toContain('Last backup:');
		expect(mounted.target.textContent).toContain('0 3 * * * /srv/potionui/potionui backup --tier config');
		expect(mounted.target.textContent).toContain('/srv/potionui/backups');
		expect(mounted.target.textContent).not.toContain('Last backup: never');
	});

	it('shows never, the empty archive list and the not-writable alert', async () => {
		vi.mocked(adminApi.getBackups).mockResolvedValue({
			success: true,
			data: overview({ archives: [], last_backup: null, mirror: null, exists: false, writable: false })
		});

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('Last backup: never');
		expect(mounted.target.textContent).toContain('No archives yet');
		expect(mounted.target.textContent).toContain('Not writable: /srv/potionui/backups');
		expect(mounted.target.textContent).not.toContain('Media mirror');
	});

	it('shows the will-be-created hint, not the alert, for a writable destination that does not exist yet', async () => {
		vi.mocked(adminApi.getBackups).mockResolvedValue({
			success: true,
			data: overview({ exists: false, writable: true })
		});

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('Will be created on the first backup');
		expect(mounted.target.textContent).not.toContain('Not writable');
	});

	it('writes a destination edit through onSettingChange', async () => {
		vi.mocked(adminApi.getBackups).mockResolvedValue({ success: true, data: overview() });

		mounted = mountPanel({ backup_destination: 'backups' });
		await settle();

		const input = mounted.input('backup-destination');
		expect(input).toBeTruthy();
		input!.value = '/mnt/nas/potionui';
		input!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(mounted.onSettingChange).toHaveBeenCalledWith('backup_destination', '/mnt/nas/potionui');
	});

	it('writes a retention edit through onSettingChange as a number', async () => {
		vi.mocked(adminApi.getBackups).mockResolvedValue({ success: true, data: overview() });

		mounted = mountPanel({ backup_retention: 7 });
		await settle();

		const input = mounted.input('backup-retention');
		expect(input).toBeTruthy();
		input!.value = '14';
		input!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(mounted.onSettingChange).toHaveBeenCalledWith('backup_retention', 14);
	});

	it('runs a backup with the saved default tier, not the dirty form value', async () => {
		vi.mocked(adminApi.getBackups).mockResolvedValue({ success: true, data: overview() });
		vi.mocked(adminApi.runBackup).mockResolvedValue({
			success: true,
			data: {
				id: 'job-1',
				status: 'done',
				tier: 'media',
				started_at: '2026-09-09T11:00:00+00:00',
				finished_at: '2026-09-09T11:00:20+00:00',
				archive: 'potionui-20260909-media.tar.zst',
				bytes: 1024,
				mirror: null,
				last_error: null
			}
		});

		mounted = mountPanel({ backup_default_tier: 'all' }, JSON.stringify({ backup_default_tier: 'media' }));
		await settle();

		expect(adminApi.runBackup).not.toHaveBeenCalled();
		mounted.button('Backup now')?.click();
		await settle();

		expect(adminApi.runBackup).toHaveBeenCalledTimes(1);
		expect(adminApi.runBackup).toHaveBeenCalledWith('media');
	});

	it('deletes an archive through the confirm modal', async () => {
		vi.mocked(adminApi.getBackups).mockResolvedValue({ success: true, data: overview() });
		vi.mocked(adminApi.deleteBackup).mockResolvedValue({ success: true });

		mounted = mountPanel();
		await settle();

		mounted.byLabel('Delete potionui-20260909-config.tar.zst')?.click();
		await settle();

		expect(adminApi.deleteBackup).not.toHaveBeenCalled();

		const confirm = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
			(b) => b.textContent?.includes('Confirm')
		);
		expect(confirm).toBeTruthy();
		confirm!.click();
		await settle();

		expect(adminApi.deleteBackup).toHaveBeenCalledWith('potionui-20260909-config.tar.zst');
	});
});
