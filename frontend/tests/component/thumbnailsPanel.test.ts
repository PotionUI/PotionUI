// @vitest-environment jsdom
//
// Render smoke test for the admin Thumbnails panel: loads the dedicated
// stats endpoint, renders the three built-in profiles from the response
// (never hardcoded in the component), reflects a non-matching settings state
// as "Custom", writes edits through onSettingChange (the five keys ride the
// shared System Settings save bar, this panel has no save of its own), and
// reloads stats once the tab reports a saved snapshot.
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ThumbnailStats } from '$lib/services/admin-api';

vi.mock('$lib/services/admin-api', () => ({
	getThumbnailStats: vi.fn(),
	startThumbnailRegeneration: vi.fn(),
	cancelThumbnailRegeneration: vi.fn(),
	getThumbnailJob: vi.fn()
}));

const adminApi = await import('$lib/services/admin-api');
const { default: ThumbnailsPanel } = await import('../../src/routes/admin/components/settings/ThumbnailsPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

function stats(overrides: Partial<ThumbnailStats> = {}): ThumbnailStats {
	return {
		settings: { sizes: ['medium'], video_fps: 12, video_seconds: 3, video_quality: 50, image_quality: 85 },
		profiles: {
			compact: { sizes: ['small'], video_fps: 8, video_seconds: 2, video_quality: 40, image_quality: 75, estimated_bytes: 1_000_000 },
			balanced: {
				sizes: ['medium'],
				video_fps: 12,
				video_seconds: 3,
				video_quality: 50,
				image_quality: 85,
				estimated_bytes: 5_000_000
			},
			full: {
				sizes: ['small', 'medium', 'large'],
				video_fps: 24,
				video_seconds: 3,
				video_quality: 50,
				image_quality: 85,
				estimated_bytes: 20_000_000
			}
		},
		active_profile: 'balanced',
		counts: { images: 5046, videos: 2104, uploads: 61, stale: 12 },
		usage: { static_bytes: 1_000_000, animated_bytes: 22_300_000_000, total_bytes: 23_300_000_000, measured_at: '2026-09-09T00:00:00Z' },
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
		component: ThumbnailsPanel as never,
		target,
		props: { settings, onSettingChange, savedSnapshot }
	});
	return {
		target,
		component,
		onSettingChange,
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

describe('ThumbnailsPanel', () => {
	it('renders the profiles and usage line from the stats response', async () => {
		vi.mocked(adminApi.getThumbnailStats).mockResolvedValue({ success: true, data: stats() });

		mounted = mountPanel();
		await settle();

		expect(mounted.button('Compact')).toBeTruthy();
		expect(mounted.button('Balanced')).toBeTruthy();
		expect(mounted.button('Full')).toBeTruthy();
		expect(mounted.target.textContent).toContain('2,104 videos');
		expect(mounted.target.textContent).toContain('5,046 images');
		expect(mounted.target.textContent).toContain('61 uploads');
		expect(mounted.target.textContent).toContain('12 files use older thumbnail settings.');
		expect(mounted.button('Save')).toBeFalsy();
	});

	it('shows Custom when the loaded settings match no built-in profile', async () => {
		vi.mocked(adminApi.getThumbnailStats).mockResolvedValue({
			success: true,
			data: stats({ settings: { sizes: ['medium', 'large'], video_fps: 12, video_seconds: 3, video_quality: 50, image_quality: 85 } })
		});

		mounted = mountPanel({ thumbnail_sizes: ['medium', 'large'], thumbnail_video_fps: 12, thumbnail_video_seconds: 3, thumbnail_video_quality: 50, thumbnail_image_quality: 85 });
		await settle();

		expect(mounted.target.textContent).toContain('Custom');
	});

	it('shows an unmeasured note when usage is null', async () => {
		vi.mocked(adminApi.getThumbnailStats).mockResolvedValue({ success: true, data: stats({ usage: null }) });

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('Not measured for S3 storage.');
	});

	it('writes a profile pick through onSettingChange instead of local state', async () => {
		vi.mocked(adminApi.getThumbnailStats).mockResolvedValue({
			success: true,
			data: stats({ settings: { sizes: ['medium'], video_fps: 12, video_seconds: 3, video_quality: 50, image_quality: 85 } })
		});

		mounted = mountPanel({ thumbnail_sizes: ['medium'], thumbnail_video_fps: 12, thumbnail_video_seconds: 3, thumbnail_video_quality: 50, thumbnail_image_quality: 85 });
		await settle();

		mounted.button('Compact')?.click();
		await settle();

		expect(mounted.onSettingChange).toHaveBeenCalledWith('thumbnail_sizes', ['small']);
		expect(mounted.onSettingChange).toHaveBeenCalledWith('thumbnail_video_fps', 8);
		expect(mounted.onSettingChange).toHaveBeenCalledWith('thumbnail_video_seconds', 2);
		expect(mounted.onSettingChange).toHaveBeenCalledWith('thumbnail_video_quality', 40);
		expect(mounted.onSettingChange).toHaveBeenCalledWith('thumbnail_image_quality', 75);
	});

	it('reloads stats once the tab reports a newer saved snapshot', async () => {
		vi.mocked(adminApi.getThumbnailStats).mockResolvedValue({ success: true, data: stats() });

		mounted = mountPanel({}, '{}');
		await settle();
		expect(adminApi.getThumbnailStats).toHaveBeenCalledTimes(1);

		mounted.component.$set({ savedSnapshot: '{"thumbnail_sizes":["small"]}' });
		await settle();

		expect(adminApi.getThumbnailStats).toHaveBeenCalledTimes(2);
	});

	it('does not reload on the initial savedSnapshot value alone', async () => {
		vi.mocked(adminApi.getThumbnailStats).mockResolvedValue({ success: true, data: stats() });

		mounted = mountPanel({}, '{}');
		await settle();

		expect(adminApi.getThumbnailStats).toHaveBeenCalledTimes(1);
	});
});
