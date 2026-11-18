<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { CopyButton, Spinner } from '$lib/components/ui';
	import PreviewClipPlayer from '$lib/components/workbench/PreviewClipPlayer.svelte';
	import type { PreviewClip } from '$lib/generation/previewClip';
	import { formatElapsed, type BatchSlot } from '$lib/generation/live/liveView';
	import type { FailureNoticeData } from './failureNotice';

	let {
		mode,
		preview,
		clip,
		suppressed = false,
		stepText,
		percent,
		elapsed,
		queued = false,
		dimensions = null,
		slots,
		thumbs,
		fulls,
		selectedSlot,
		onSelectSlot,
		failure = null,
		isAdmin = false
	}: {
		mode: 'live' | 'ended';
		preview: string | null;
		clip: PreviewClip | null;
		suppressed?: boolean;
		stepText: string;
		percent: number | null;
		elapsed: number | null;
		queued?: boolean;
		dimensions?: string | null;
		slots: BatchSlot[];
		thumbs: (string | null)[];
		fulls: (string | null)[];
		selectedSlot: number | null;
		onSelectSlot: (index: number | null) => void;
		failure?: FailureNoticeData | null;
		isAdmin?: boolean;
	} = $props();

	let finished = $derived(slots.filter((slot) => slot.state === 'done').length);
	let activeIndex = $derived(slots.find((slot) => slot.state === 'active' || slot.state === 'failed')?.index ?? null);
	let shownThumb = $derived(selectedSlot !== null ? (fulls[selectedSlot] ?? thumbs[selectedSlot] ?? null) : null);
	let counter = $derived(
		slots.length > 1
			? `${(selectedSlot ?? activeIndex ?? 0) + 1} / ${slots.length}`
			: null
	);
	let ruler = $derived(mode === 'live' && percent !== null ? percent : 0);
</script>

<div class="absolute inset-0 flex flex-col" data-live-stage data-live-mode={mode}>
	<div class="relative min-h-0 flex-1">
		{#if mode === 'ended' && selectedSlot === null}
			<div class="flex h-full flex-col items-center justify-center gap-3 px-6 text-center" data-live-failure>
				<div class="flex h-10 w-10 items-center justify-center rounded border border-danger/30 bg-danger/10 text-danger">
					<Icon name="warning" className="h-5 w-5" />
				</div>
				<h3 class="text-lg font-semibold text-fg">
					{failure?.title === 'Cancelled' ? 'Generation cancelled' : 'Generation failed'}
				</h3>
				{#if failure}
					<p class="max-w-md whitespace-pre-line text-base text-fg-muted [overflow-wrap:anywhere]">{failure.message}</p>
					{#if failure.errorId}
						<div class="flex items-center gap-1.5">
							<span class="text-sm text-fg-subtle">Error ID</span>
							<span class="font-mono text-sm tabular-nums text-fg-muted">{failure.errorId}</span>
							<CopyButton text={failure.errorId} ariaLabel="Copy error ID" size="xs" />
						</div>
					{/if}
				{/if}
				<p class="text-sm text-fg-subtle">Your settings are on the right.</p>
			</div>
		{:else}
			<div class="absolute left-4 top-3 z-20 flex flex-wrap items-center gap-2 font-mono text-xs tabular-nums text-white">
				{#if selectedSlot === null}
					<span class="rounded bg-signal-solid px-2 py-1 font-medium uppercase tracking-[0.07em]" data-live-chip>Preview</span>
				{/if}
				{#if counter}<span>{counter}</span>{/if}
				{#if dimensions}<span>{dimensions}</span>{/if}
				{#if clip && selectedSlot === null}<span>Looping preview · low resolution</span>{/if}
			</div>
			{#if shownThumb}
				<img src={shownThumb} alt="Finished image {(selectedSlot ?? 0) + 1}" class="absolute inset-0 h-full w-full object-contain p-8" data-live-finished />
			{:else if clip}
				<PreviewClipPlayer {clip} progress={(percent ?? 0) / 100} {isAdmin} ruler={false} chip={false} />
			{:else if preview}
				<img src={preview} alt="Live preview" class="absolute inset-0 h-full w-full object-contain p-8" data-live-preview />
			{:else}
				<div class="flex h-full flex-col items-center justify-center gap-3 text-fg-subtle" data-live-waiting>
					{#if suppressed}
						<Icon name="eye-off" className="h-6 w-6" />
						<span class="text-sm">Preview hidden for this generation</span>
					{:else}
						<Spinner size="md" />
						<span class="text-sm">{queued ? 'Waiting in the queue' : 'Waiting for the first preview'}</span>
					{/if}
				</div>
			{/if}
		{/if}
	</div>

	{#if mode === 'live'}
		<div class="shrink-0 px-4 pb-3 pt-2" data-live-progress>
			<div class="mb-2 flex items-baseline justify-between gap-3 text-sm text-fg">
				<span class="min-w-0 truncate" data-live-step>{stepText}</span>
				<span class="shrink-0 font-mono tabular-nums text-fg-muted">
					{#if percent !== null}{percent}%{/if}
					<span class="ml-2" data-live-elapsed>{formatElapsed(elapsed)}<span class="max-sm:hidden">{' elapsed'}</span></span>
				</span>
			</div>
			<div class="tick-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={percent ?? undefined} aria-label="Generation progress">
				<div class="tick-fill bg-signal-solid transition-all duration-300" style="width: {ruler}%"></div>
			</div>
		</div>
	{/if}

	{#if slots.length > 1}
		<div class="flex shrink-0 items-center gap-3 overflow-x-auto border-t border-line bg-surface-1 px-4 py-2.5" data-live-batch>
			<span class="font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">Batch</span>
			<div class="flex items-center gap-2">
				{#each slots as slot (slot.index)}
					{@const thumb = thumbs[slot.index] ?? null}
					<button
						type="button"
						class="relative flex h-12 w-24 shrink-0 items-center justify-center overflow-hidden rounded border font-mono text-xs tabular-nums
							{slot.state === 'done'
							? 'border-line-strong bg-surface-2 hover:border-line-hover'
							: slot.state === 'active'
								? 'border-signal bg-surface-2'
								: slot.state === 'failed'
									? 'border-danger/60 bg-danger/10 text-danger'
									: 'cursor-default border-dashed border-line text-fg-subtle'}
							{selectedSlot === slot.index || (selectedSlot === null && (slot.state === 'active' || slot.state === 'failed')) ? 'ring-1 ring-fg' : ''}"
						disabled={slot.state === 'waiting'}
						aria-label="Image {slot.index + 1}, {slot.state === 'done' ? 'ready' : slot.state === 'waiting' ? 'waiting' : slot.state === 'failed' ? 'failed' : 'generating'}"
						aria-pressed={selectedSlot === slot.index}
						data-live-slot={slot.state}
						onclick={() => onSelectSlot(slot.state === 'done' ? slot.index : null)}
					>
						{#if slot.state === 'done' && thumb}
							<img src={thumb} alt="" class="h-full w-full object-cover" />
						{:else if slot.state === 'active' && preview}
							<img src={preview} alt="" class="h-full w-full object-cover" />
						{:else if slot.state === 'failed'}
							<Icon name="warning" className="h-4 w-4" />
						{:else}
							{slot.index + 1}
						{/if}
						{#if slot.state === 'done'}
							<span class="absolute bottom-0.5 right-0.5 text-success"><Icon name="check" className="h-3 w-3" /></span>
						{/if}
					</button>
				{/each}
			</div>
			<span class="ml-auto shrink-0 font-mono text-xs tabular-nums text-fg-subtle" data-live-count>
				{finished} of {slots.length} {mode === 'live' ? 'ready' : 'finished'}
			</span>
		</div>
	{/if}
</div>

<style>
	.tick-track {
		position: relative;
		height: 4px;
		background: rgb(var(--surface-3));
		overflow: hidden;
	}
	.tick-fill {
		height: 100%;
	}
	.tick-track::after {
		content: '';
		position: absolute;
		inset: 0;
		background: repeating-linear-gradient(
			90deg,
			transparent 0,
			transparent 7px,
			rgb(var(--canvas) / 0.6) 7px,
			rgb(var(--canvas) / 0.6) 8px
		);
		pointer-events: none;
	}
</style>
