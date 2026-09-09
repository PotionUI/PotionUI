<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import type { ThumbnailProfileName, ThumbnailStats } from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Button, Alert, Spinner, SegmentedControl, Switch, Badge, Input } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { formatBytes, formatCount } from '$lib/utils/format';
	import { matchProfile, parseThumbnailSettings, type ThumbnailValues } from './thumbnailProfiles';

	let {
		settings,
		onSettingChange,
		savedSnapshot
	}: {
		settings: Record<string, any>;
		onSettingChange: (key: string, value: unknown) => void;
		/** The tab's saved-baseline snapshot (`snapshot`) - bumping it means
		 * the shared footer just persisted a batch that may include our five
		 * keys, so stats (stale count, active profile, usage) are stale. */
		savedSnapshot: string;
	} = $props();

	const PROFILE_ITEMS = [
		{ id: 'compact', label: 'Compact' },
		{ id: 'balanced', label: 'Balanced' },
		{ id: 'full', label: 'Full' }
	];
	const SIZE_TOGGLES: { id: 'small' | 'medium' | 'large'; label: string; hint: string }[] = [
		{ id: 'small', label: 'Small', hint: 'Grid thumbnails and pickers.' },
		{ id: 'medium', label: 'Medium', hint: 'Gallery cards and detail previews.' },
		{ id: 'large', label: 'Large', hint: 'Full-size hover and lightbox previews.' }
	];
	const FIELD_TO_SETTINGS_KEY: Record<Exclude<keyof ThumbnailValues, 'sizes'>, string> = {
		video_fps: 'thumbnail_video_fps',
		video_seconds: 'thumbnail_video_seconds',
		video_quality: 'thumbnail_video_quality',
		image_quality: 'thumbnail_image_quality'
	};

	let loading = $state(true);
	let error = $state<string | null>(null);
	let stats = $state<ThumbnailStats | null>(null);

	// The tab's `settings` prop is the source of truth for the five keys -
	// every edit below writes through `onSettingChange` and this recomputes.
	let values = $derived(parseThumbnailSettings(settings));

	let advancedOpen = $state(false);
	let confirmOpen = $state(false);
	let regenerating = $state(false);
	let cancelling = $state(false);

	let pollHandle: ReturnType<typeof setInterval> | null = null;

	let selectedProfile = $derived(stats ? matchProfile(values, stats.profiles) : 'custom');
	let job = $derived(stats?.job ?? null);
	let jobActive = $derived(job?.status === 'running' || job?.status === 'cancelling');
	let jobPercent = $derived(job && job.total > 0 ? Math.round((job.done / job.total) * 100) : 0);
	let jobCurrentBasename = $derived(job?.current ? job.current.split('/').pop() : null);
	let staleCount = $derived(stats?.counts.stale ?? 0);

	onMount(load);
	onDestroy(stopPolling);

	// The shared save bar persists our keys on its own schedule - once it
	// does, `savedSnapshot` (the tab's `snapshot`) moves and stats (stale
	// count, active profile, usage) need a refresh. The initial run is a
	// no-op: `loading` is still true at that point, before onMount's own
	// load() has resolved.
	$effect(() => {
		savedSnapshot;
		untrack(() => {
			if (!loading) load();
		});
	});

	async function load() {
		loading = true;
		error = null;
		try {
			const response = await adminApi.getThumbnailStats();
			if (!response.success || !response.data) {
				error = response.message ?? 'Failed to load thumbnail settings.';
				return;
			}
			stats = response.data;
			syncPolling();
		} catch (e: any) {
			logger.error('Failed to load thumbnail settings:', e);
			error = e.response?.data?.message || e.message || 'Failed to load thumbnail settings.';
		} finally {
			loading = false;
		}
	}

	function applyProfile(name: string) {
		if (!stats) return;
		const p = stats.profiles[name as ThumbnailProfileName];
		if (!p) return;
		onSettingChange('thumbnail_sizes', [...p.sizes]);
		onSettingChange('thumbnail_video_fps', p.video_fps);
		onSettingChange('thumbnail_video_seconds', p.video_seconds);
		onSettingChange('thumbnail_video_quality', p.video_quality);
		onSettingChange('thumbnail_image_quality', p.image_quality);
	}

	function toggleSize(size: string) {
		const sizes = values.sizes.includes(size) ? values.sizes.filter((s) => s !== size) : [...values.sizes, size];
		onSettingChange('thumbnail_sizes', sizes);
	}

	function updateField(key: Exclude<keyof ThumbnailValues, 'sizes'>, raw: string) {
		const parsed = Number(raw);
		onSettingChange(FIELD_TO_SETTINGS_KEY[key], Number.isFinite(parsed) ? parsed : 0);
	}

	function openConfirm() {
		confirmOpen = true;
	}

	function closeConfirm() {
		confirmOpen = false;
	}

	async function regenerate() {
		regenerating = true;
		try {
			const response = await adminApi.startThumbnailRegeneration();
			confirmOpen = false;
			if (response.success && response.data) {
				stats = stats ? { ...stats, job: response.data } : stats;
				startPolling();
			} else {
				toasts.error(response.message ?? 'Failed to start regeneration.');
			}
		} catch (e: any) {
			confirmOpen = false;
			const status = e?.response?.status;
			const message = e.response?.data?.message || e.message || 'Failed to start regeneration.';
			toasts.error(message);
			if (status === 409) await load();
		} finally {
			regenerating = false;
		}
	}

	async function cancel() {
		cancelling = true;
		try {
			const response = await adminApi.cancelThumbnailRegeneration();
			if (response.success && response.data) {
				stats = stats ? { ...stats, job: response.data } : stats;
			}
		} catch (e: any) {
			toasts.error(e.response?.data?.message || e.message || 'Failed to cancel regeneration.');
		} finally {
			cancelling = false;
		}
	}

	function startPolling() {
		stopPolling();
		pollHandle = setInterval(pollJob, 2000);
	}

	function stopPolling() {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	}

	function syncPolling() {
		const status = stats?.job?.status;
		if (status === 'running' || status === 'cancelling') startPolling();
		else stopPolling();
	}

	async function pollJob() {
		try {
			const response = await adminApi.getThumbnailJob();
			if (!response.success) return;
			const nextJob = response.data ?? null;
			stats = stats ? { ...stats, job: nextJob } : stats;
			if (!nextJob || (nextJob.status !== 'running' && nextJob.status !== 'cancelling')) {
				stopPolling();
				await load();
			}
		} catch (e) {
			logger.error('Failed to poll thumbnail regeneration job:', e);
		}
	}

	function sizesLabel(sizes: string[]): string {
		return sizes.length > 0 ? sizes.join(', ') : 'none';
	}
</script>

<div class="space-y-5">
	<DetailSection label="Thumbnails" padded={false}>
		<div class="px-4 sm:px-5 py-4 space-y-4">
			<p class="text-sm text-fg-muted">
				Preview images shown in galleries. Smaller profiles use less disk.
			</p>

			{#if loading}
				<Spinner size="sm" />
			{:else}
				{#if error}
					<Alert variant="danger" icon>{error}</Alert>
				{/if}

				{#if stats}
					<div class="flex items-center gap-2">
						<SegmentedControl
							items={PROFILE_ITEMS}
							selected={selectedProfile === 'custom' ? '' : selectedProfile}
							onSelect={applyProfile}
							ariaLabel="Thumbnail profile"
						/>
						{#if selectedProfile === 'custom'}
							<Badge variant="signal" size="sm">Custom</Badge>
						{/if}
					</div>

					<div class="grid grid-cols-3 gap-2">
						{#each PROFILE_ITEMS as item (item.id)}
							{@const p = stats.profiles[item.id as ThumbnailProfileName]}
							<div class="rounded border border-line-strong px-2.5 py-2 text-2xs text-fg-muted space-y-0.5">
								<p class="truncate">{sizesLabel(p.sizes)} · {p.video_fps}fps</p>
								<p class="font-mono tabular-nums text-fg-subtle">about {formatBytes(p.estimated_bytes)}</p>
							</div>
						{/each}
					</div>

					<div class="pt-1">
						{#if stats.usage}
							<p class="font-mono text-xs tabular-nums text-fg-muted">
								Thumbnails use {formatBytes(stats.usage.total_bytes)} · {formatCount(stats.counts.videos)} videos · {formatCount(
									stats.counts.images
								)} images · {formatCount(stats.counts.uploads)} uploads
							</p>
						{:else}
							<p class="text-xs text-fg-muted">Not measured for S3 storage.</p>
						{/if}
					</div>
				{/if}
			{/if}
		</div>
	</DetailSection>

	{#if !loading && stats}
		<DetailSection label="Advanced" collapsible bind:open={advancedOpen} padded={false}>
			<div class="px-4 sm:px-5 divide-y divide-line">
				{#if values.sizes.length === 0}
					<div class="py-4">
						<Alert variant="warning" icon>Pick at least one size.</Alert>
					</div>
				{/if}
				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<p class="text-sm font-medium text-fg mb-1">Sizes</p>
						<p class="text-sm text-fg-muted">Which thumbnail sizes are generated for each file.</p>
					</div>
					<div class="flex flex-col gap-2 flex-shrink-0">
						{#each SIZE_TOGGLES as toggle (toggle.id)}
							<div class="flex items-center gap-2 justify-end">
								<span class="text-xs text-fg-muted text-right w-40">{toggle.hint}</span>
								<Switch
									id={`thumbnail-size-${toggle.id}`}
									checked={values.sizes.includes(toggle.id)}
									onchange={() => toggleSize(toggle.id)}
									label={`${toggle.label} thumbnails`}
								/>
								<span class="text-sm text-fg w-16">{toggle.label}</span>
							</div>
						{/each}
					</div>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="thumbnail-video-fps" class="block text-sm font-medium text-fg mb-1">Video FPS</label>
						<p class="text-sm text-fg-muted">Frames per second of hover previews. Lower is smaller.</p>
					</div>
					<Input
						id="thumbnail-video-fps"
						type="number"
						min="1"
						class="w-24 font-mono tabular-nums flex-shrink-0"
						value={String(values.video_fps)}
						oninput={(e: Event) => updateField('video_fps', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="thumbnail-video-seconds" class="block text-sm font-medium text-fg mb-1">Video length</label>
						<p class="text-sm text-fg-muted">Seconds of each hover preview clip.</p>
					</div>
					<Input
						id="thumbnail-video-seconds"
						type="number"
						min="1"
						class="w-24 font-mono tabular-nums flex-shrink-0"
						value={String(values.video_seconds)}
						oninput={(e: Event) => updateField('video_seconds', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="thumbnail-video-quality" class="block text-sm font-medium text-fg mb-1">Video quality</label>
						<p class="text-sm text-fg-muted">Encoding quality of hover preview clips. Lower is smaller.</p>
					</div>
					<Input
						id="thumbnail-video-quality"
						type="number"
						min="1"
						max="100"
						class="w-24 font-mono tabular-nums flex-shrink-0"
						value={String(values.video_quality)}
						oninput={(e: Event) => updateField('video_quality', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="thumbnail-image-quality" class="block text-sm font-medium text-fg mb-1">Image quality</label>
						<p class="text-sm text-fg-muted">Encoding quality of static thumbnails. Lower is smaller.</p>
					</div>
					<Input
						id="thumbnail-image-quality"
						type="number"
						min="1"
						max="100"
						class="w-24 font-mono tabular-nums flex-shrink-0"
						value={String(values.image_quality)}
						oninput={(e: Event) => updateField('image_quality', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>
			</div>
		</DetailSection>

		<DetailSection label="Regenerate" padded={false}>
			<div class="px-4 sm:px-5 py-4 space-y-3">
				{#if jobActive && job}
					<div class="space-y-2">
						<div class="h-1 rounded-full bg-surface-3 overflow-hidden">
							<div class="h-full bg-signal rounded-full transition-[width]" style="width: {jobPercent}%"></div>
						</div>
						<div class="flex items-center justify-between gap-3">
							<span class="font-mono text-xs tabular-nums text-fg-muted">
								{formatCount(job.done)} / {formatCount(job.total)} · {formatCount(job.failed)} failed
							</span>
							<Button variant="secondary" size="sm" loading={cancelling} disabled={cancelling || job.status === 'cancelling'} onclick={cancel}>
								Cancel
							</Button>
						</div>
						{#if jobCurrentBasename}
							<p class="text-2xs text-fg-subtle truncate" title={job.current ?? ''}>{jobCurrentBasename}</p>
						{/if}
					</div>
				{:else}
					{#if job?.status === 'failed' && job.last_error}
						<Alert variant="danger" icon>{job.last_error}</Alert>
					{/if}
					<div class="flex items-center justify-between gap-3">
						<p class="text-sm text-fg-muted">
							{formatCount(staleCount)} files use older thumbnail settings.
						</p>
						<Button variant="secondary" size="sm" disabled={staleCount === 0} onclick={openConfirm}>Regenerate</Button>
					</div>
				{/if}
			</div>
		</DetailSection>
	{/if}
</div>

<ConfirmModal
	isOpen={confirmOpen}
	title="Regenerate thumbnails"
	message={`Rebuilds previews for ${staleCount} files with the saved profile, one file at a time. The gallery keeps working while it runs.`}
	variant="info"
	busy={regenerating}
	on:confirm={regenerate}
	on:cancel={closeConfirm}
/>
