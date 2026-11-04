<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { TileLetter, TileTick } from '$lib/elixirs/choice';
	import type { OutputEntry } from '$lib/elixirs/types';
	import { formatDuration, stillSource, tileKind, tileName, videoSource } from '$lib/elixirs/tiles';

	interface Props {
		entry: OutputEntry;
		onopen?: (entry: OutputEntry) => void;
		tick?: TileTick | null;
		ontick?: (entry: OutputEntry) => void;
		letters?: TileLetter[];
		opener?: string | null;
	}

	let { entry, onopen, tick = null, ontick, letters = [], opener = null }: Props = $props();

	let video = $state<HTMLVideoElement>();
	let playing = $state(false);
	let duration = $state<number | null>(null);
	let failed = $state(false);
	let ratio = $state<string | null>(null);

	function measure(width: number, height: number) {
		if (width > 0 && height > 0) ratio = `${width} / ${height}`;
	}
	let lastPointer = 'mouse';

	let kind = $derived(tileKind(entry));
	let name = $derived(tileName(entry));
	let still = $derived(stillSource(entry));
	let clip = $derived(kind === 'video' ? videoSource(entry) : null);

	function play() {
		if (!video) return;
		video.muted = true;
		video.play().then(
			() => (playing = true),
			() => (playing = false)
		);
	}

	function stop() {
		if (!video) return;
		video.pause();
		video.currentTime = 0.1;
		playing = false;
	}

	function activate() {
		if (kind === 'video' && lastPointer === 'touch') {
			if (playing) stop();
			else play();
			return;
		}
		onopen?.(entry);
	}
</script>

<div
	class="tile group relative w-full overflow-hidden rounded border border-line-strong bg-canvas hover:border-line-hover"
	class:chosen={tick?.checked}
	style:aspect-ratio={ratio}
	onmouseenter={() => kind === 'video' && play()}
	onmouseleave={() => kind === 'video' && stop()}
	role="presentation"
>
	<button
		type="button"
		class="tile-face block h-full w-full"
		aria-label="Open {name}"
		data-viewer-opener={opener}
		onpointerdown={(event) => (lastPointer = event.pointerType)}
		onkeydown={() => (lastPointer = 'keyboard')}
		onfocus={() => kind === 'video' && lastPointer !== 'touch' && play()}
		onblur={() => kind === 'video' && stop()}
		onclick={activate}
	>
		{#if kind === 'video' && clip && !failed}
			<video
				bind:this={video}
				src={clip}
				class="h-full w-full object-cover"
				muted
				loop
				playsinline
				preload="metadata"
				aria-hidden="true"
				onloadedmetadata={() => {
					duration = video?.duration ?? null;
					if (video) measure(video.videoWidth, video.videoHeight);
				}}
				onerror={() => (failed = true)}
			></video>
		{:else if still && !failed}
			<img
				src={still}
				alt=""
				class="h-full w-full object-cover"
				loading="lazy"
				onload={(event) => measure(event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)}
				onerror={() => (failed = true)}
			/>
		{:else}
			<span class="flex h-full w-full items-center justify-center text-fg-subtle">
				<Icon name={kind === 'video' ? 'film' : kind === 'audio' ? 'audio' : 'image'} className="h-5 w-5" />
			</span>
		{/if}
	</button>

	{#if tick}
		<span class="absolute left-1 top-1 z-10">
			<button
				type="button"
				role="checkbox"
				class="tick flex h-6 w-6 items-center justify-center rounded"
				aria-checked={tick.checked}
				aria-disabled={tick.locked}
				aria-label="Send {name} to {tick.label}"
				onclick={(event) => {
					event.stopPropagation();
					if (!tick?.locked) ontick?.(entry);
				}}
			>
				<span class="tick-box flex h-5 w-5 items-center justify-center rounded {tick.checked ? 'bg-signal-solid text-white' : 'bg-black/45 text-transparent ring-1 ring-white/60 backdrop-blur-sm hover:bg-black/70'}">
					{#if tick.checked}
						<Icon name="check" className="h-3.5 w-3.5" />
					{/if}
				</span>
			</button>
		</span>
	{/if}

	{#if letters.length > 0}
		<span class="absolute bottom-1.5 right-1.5 flex gap-1">
			{#each letters as lane (lane.label)}
				<Tooltip text={`Sends to ${lane.label}`}>
					<span class="lane-letter rounded px-1 text-center text-sm {lane.active ? 'bg-signal-solid text-white' : 'bg-black/60 text-white backdrop-blur-sm'}">{lane.letter}</span>
				</Tooltip>
			{/each}
		</span>
	{/if}

	{#if kind !== 'video' && entry?.original_filename}
		<span class="tile-name pointer-events-none absolute bottom-1 left-1 max-w-[calc(100%-0.5rem)] truncate rounded px-1 font-mono text-sm">{entry.original_filename}</span>
	{/if}

	{#if kind === 'video'}
		<span class="tile-badge pointer-events-none absolute bottom-1.5 left-1.5 inline-flex items-center gap-1 rounded bg-black/60 px-1.5 text-sm text-white backdrop-blur-sm">
			<Icon name={playing ? 'pause' : 'play'} className="h-3 w-3" />
			{#if duration !== null}
				<span class="font-mono tabular-nums">{formatDuration(duration)}</span>
			{/if}
		</span>
	{/if}

	<span class="tile-open absolute right-1.5 top-1.5">
		<Tooltip text={`Open ${name}`} position="top">
			<button
				type="button"
				class="tile-open-button flex h-6 w-6 items-center justify-center rounded bg-black/60 text-white backdrop-blur-sm hover:bg-black/80"
				aria-label="Open {name} large"
				data-viewer-opener={opener}
				onclick={() => onopen?.(entry)}
			>
				<Icon name="eyes" className="h-3.5 w-3.5" />
			</button>
		</Tooltip>
	</span>
</div>

<style>
	.tile {
		aspect-ratio: 5 / 4;
	}
	.tile-name {
		line-height: 16px;
		background: rgb(var(--scrim) / 0.6);
		color: rgb(var(--on-scrim));
	}
	.chosen {
		outline: 2px solid rgb(var(--signal));
		outline-offset: -1px;
	}
	.tick[aria-disabled='true'] {
		cursor: default;
	}
	:global(:where(html:not([data-input='pointer']))) .tick:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 0;
	}
	.lane-letter {
		min-width: 18px;
		line-height: 18px;
	}
	:global(:where(html:not([data-input='pointer']))) .tile-face:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: -2px;
	}
	.tile-badge {
		line-height: 18px;
	}
	.tile-open {
		display: none;
	}
	.tile:hover .tile-open,
	.tile:focus-within .tile-open {
		display: block;
	}
	@media (hover: none) {
		.tile-open {
			display: block;
		}
	}
</style>
