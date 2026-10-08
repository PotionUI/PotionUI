<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Spinner } from '$lib/components/ui';
	import type { GridCell } from '../compareStore.svelte';
	import type { VideoGroup } from './videoSync';
	import { cellAxisSummary, cellProgressLabel, cellStatusLabel, formatSeconds } from './gridModel';

	let {
		cell,
		index,
		ordinal = null,
		seconds = null,
		selected = false,
		aspect = 1,
		videoUrl = null,
		group = null,
		onOpen,
		onRetry,
		onAspect
	}: {
		cell: GridCell;
		index: number;
		ordinal?: number | null;
		seconds?: number | null;
		selected?: boolean;
		aspect?: number;
		videoUrl?: string | null;
		group?: VideoGroup | null;
		onOpen?: (index: number) => void;
		onRetry?: (index: number) => void;
		onAspect?: (aspect: number) => void;
	} = $props();

	let isVideo = $derived(cell.mediaType === 'video');
	let percent = $derived(
		cell.progress && cell.progress.total > 0
			? Math.max(4, Math.round((cell.progress.step / cell.progress.total) * 100))
			: 4
	);
	let label = $derived(
		`Cell ${index + 1}, ${cellAxisSummary(cell.axisValues) || 'no axis values'}, ${cellStatusLabel(cell.status).toLowerCase()}`
	);
	let openable = $derived(cell.status !== 'empty' && cell.status !== 'queued');

	function activate() {
		if (openable) onOpen?.(index);
	}

	function handleKey(event: KeyboardEvent) {
		if (event.key !== 'Enter' && event.key !== ' ') return;
		event.preventDefault();
		activate();
	}

	function retry(event: Event) {
		event.stopPropagation();
		onRetry?.(index);
	}

	function reportImage(event: Event) {
		const img = event.currentTarget as HTMLImageElement;
		if (img.naturalWidth > 0 && img.naturalHeight > 0) onAspect?.(img.naturalWidth / img.naturalHeight);
	}

	function reportVideo(event: Event) {
		const video = event.currentTarget as HTMLVideoElement;
		if (video.videoWidth > 0 && video.videoHeight > 0) onAspect?.(video.videoWidth / video.videoHeight);
	}

	function videoCell(node: HTMLVideoElement, target: VideoGroup | null) {
		let current = target;
		current?.add(node);
		const observer =
			typeof IntersectionObserver === 'undefined'
				? null
				: new IntersectionObserver(
						(entries) => {
							const entry = entries[entries.length - 1];
							if (entry) current?.setVisible(node, entry.isIntersecting);
						},
						{ threshold: 0.2 }
					);
		observer?.observe(node);
		const handleEnded = () => current?.ended(node);
		node.addEventListener('ended', handleEnded);
		return {
			update(next: VideoGroup | null) {
				if (next === current) return;
				current?.remove(node);
				current = next;
				current?.add(node);
			},
			destroy() {
				node.removeEventListener('ended', handleEnded);
				observer?.disconnect();
				current?.remove(node);
			}
		};
	}
</script>

<div
	role="button"
	tabindex="0"
	aria-label={label}
	data-testid="compare-cell"
	data-cell-index={index}
	data-cell-state={cell.status}
	class="relative flex min-w-0 select-none flex-col overflow-hidden rounded-lg border transition-colors duration-100 {cell.status ===
	'failed'
		? 'border-danger/40 bg-danger/10'
		: cell.status === 'completed' || cell.status === 'running'
			? 'border-line-strong bg-black'
			: 'border-dashed border-line bg-surface-1'} {openable ? 'cursor-pointer hover:border-line-hover' : ''} {selected
		? 'ring-1 ring-signal'
		: ''}"
	style="aspect-ratio: {aspect}"
	onclick={activate}
	onkeydown={handleKey}
>
	{#if cell.status === 'completed'}
		{#if isVideo && videoUrl}
			<video
				use:videoCell={group}
				src={videoUrl}
				poster={cell.thumbnailUrl ?? undefined}
				class="h-full w-full object-contain"
				muted
				loop
				autoplay
				playsinline
				preload="metadata"
				onloadedmetadata={reportVideo}
			></video>
		{:else if cell.thumbnailUrl}
			<img
				src={cell.thumbnailUrl}
				alt=""
				class="h-full w-full object-contain"
				onload={reportImage}
			/>
		{:else}
			<div class="flex h-full w-full items-center justify-center">
				<Icon name="photo" className="h-8 w-8 text-fg-subtle" strokeWidth={1.5} />
			</div>
		{/if}
		{#if seconds !== null}
			<span
				data-testid="compare-cell-time"
				class="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-2xs tabular-nums text-fg"
			>
				{formatSeconds(seconds)}
			</span>
		{/if}
	{:else if cell.status === 'running'}
		{#if cell.previewUrl}
			<img
				src={cell.previewUrl}
				alt=""
				class="absolute inset-0 h-full w-full scale-110 object-cover opacity-70 blur-md"
			/>
		{/if}
		<div class="relative flex h-full w-full flex-col items-center justify-center gap-1.5">
			<Spinner size="sm" />
			{#if cell.progress}
				<span data-testid="compare-cell-steps" class="font-mono text-xs tabular-nums text-fg">
					{cellProgressLabel(cell.progress)}
				</span>
			{:else}
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Running</span>
			{/if}
		</div>
		<div class="tick-track absolute bottom-0 left-0 right-0" data-testid="compare-cell-tick">
			<div class="tick-fill bg-signal-solid" style="width: {percent}%"></div>
		</div>
	{:else if cell.status === 'queued'}
		<div class="dotted flex h-full w-full flex-col items-center justify-center gap-1">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Queued</span>
			{#if ordinal !== null}
				<span data-testid="compare-cell-ordinal" class="font-mono text-xs tabular-nums text-fg-subtle">#{ordinal}</span>
			{/if}
		</div>
	{:else if cell.status === 'failed'}
		<div class="flex h-full w-full flex-col items-center justify-center gap-2 p-3 text-center">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-danger">Failed</span>
			{#if cell.error}
				<p data-testid="compare-cell-error" class="line-clamp-3 text-xs text-fg-muted">{cell.error}</p>
			{/if}
			<Button variant="secondary" size="xs" icon="refresh" onclick={retry}>Retry</Button>
		</div>
	{:else if cell.status === 'deleted'}
		<div class="dotted flex h-full w-full flex-col items-center justify-center gap-2 p-3 text-center">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Deleted</span>
			<Button variant="secondary" size="xs" icon="refresh" onclick={retry}>Retry</Button>
		</div>
	{:else if cell.status === 'cancelled'}
		<div class="dotted flex h-full w-full items-center justify-center">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Cancelled</span>
		</div>
	{:else}
		<div class="dotted h-full w-full" data-testid="compare-cell-empty"></div>
	{/if}
</div>

<style>
	.dotted {
		background-image: radial-gradient(rgb(var(--line-strong)) 1px, transparent 1px);
		background-size: 12px 12px;
	}
	.tick-track {
		height: 4px;
		background: rgba(0, 0, 0, 0.45);
		overflow: hidden;
	}
	.tick-fill {
		height: 100%;
		transition: width 300ms;
	}
</style>
