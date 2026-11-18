<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton } from '$lib/components/ui';
	import { pageOwnsKeys } from '$lib/utils/shortcutGuard';
	import { formatEditorTime } from '$lib/tool-tabs/video/videoState';
	import { fixed2 } from '$lib/tool-tabs/video/videoSteps';
	import { fromDrag, type AiRange } from '$lib/tool-tabs/video/aiRange';

	let {
		duration,
		playhead,
		inPoint,
		outPoint,
		outputLength,
		handles = false,
		playing = false,
		loop = true,
		muted = false,
		frameStep = 1 / 24,
		frames = [],
		tileWidth = 0,
		peaks = null,
		markers = [],
		fadeIn = 0,
		fadeOut = 0,
		replacement = null,
		range = null,
		rangeMode = false,
		rangeName = 'Section',
		rangeMarker = null,
		compact = false,
		active = true,
		onstripwidth,
		onseek,
		onin,
		onout,
		ontoggleplay,
		onstep,
		ontoggleloop,
		ontogglemute,
		onrange,
		onrangestart,
		onrangeend
	}: {
		duration: number;
		playhead: number;
		inPoint: number;
		outPoint: number;
		outputLength: number;
		handles?: boolean;
		playing?: boolean;
		loop?: boolean;
		muted?: boolean;
		frameStep?: number;
		frames?: (string | null)[];
		tileWidth?: number;
		peaks?: number[] | null;
		markers?: { at: number; label: string }[];
		fadeIn?: number;
		fadeOut?: number;
		replacement?: { name: string } | null;
		range?: AiRange | null;
		rangeMode?: boolean;
		rangeName?: string;
		rangeMarker?: { at: number; label: string } | null;
		compact?: boolean;
		active?: boolean;
		onstripwidth?: (width: number) => void;
		onseek: (seconds: number) => void;
		onin: (seconds: number) => void;
		onout: (seconds: number) => void;
		ontoggleplay: () => void;
		onstep: (direction: -1 | 1) => void;
		ontoggleloop?: () => void;
		ontogglemute?: () => void;
		onrange?: (range: AiRange) => void;
		onrangestart?: (seconds: number) => void;
		onrangeend?: (seconds: number) => void;
	} = $props();

	let track = $state<HTMLElement | null>(null);
	let stripWidth = $state(0);

	$effect(() => {
		if (stripWidth > 0) onstripwidth?.(stripWidth);
	});

	const pct = (seconds: number) => (duration > 0 ? Math.max(0, Math.min(1, seconds / duration)) * 100 : 0);
	let inPct = $derived(pct(inPoint));
	let outPct = $derived(pct(outPoint));
	let headPct = $derived(pct(playhead));
	let rangeStartPct = $derived(range ? pct(range.start) : 0);
	let rangeEndPct = $derived(range ? pct(range.end) : 0);
	let trimmed = $derived(duration > 0 && (inPoint > 0.005 || outPoint < duration - 0.005));

	let ticks = $derived.by(() => {
		if (!(duration > 0)) return [] as number[];
		const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
		const step = steps.find((value) => duration / value <= (compact ? 6 : 10)) ?? 600;
		const list: number[] = [];
		for (let t = 0; t <= duration + 1e-6; t += step) list.push(Math.round(t * 1000) / 1000);
		return list;
	});

	function timeAt(clientX: number): number {
		if (!track || !(duration > 0)) return 0;
		const box = track.getBoundingClientRect();
		if (box.width <= 0) return 0;
		return Math.max(0, Math.min(1, (clientX - box.left) / box.width)) * duration;
	}

	function drag(event: PointerEvent, apply: (seconds: number) => void) {
		if (event.button !== 0) return;
		event.preventDefault();
		event.stopPropagation();
		const target = event.currentTarget as HTMLElement;
		target.setPointerCapture?.(event.pointerId);
		apply(timeAt(event.clientX));
		const move = (next: PointerEvent) => apply(timeAt(next.clientX));
		const up = () => {
			target.removeEventListener('pointermove', move);
			target.removeEventListener('pointerup', up);
			target.removeEventListener('pointercancel', up);
		};
		target.addEventListener('pointermove', move);
		target.addEventListener('pointerup', up);
		target.addEventListener('pointercancel', up);
	}

	function markDrag(event: PointerEvent) {
		if (event.button !== 0) return;
		event.preventDefault();
		event.stopPropagation();
		const target = event.currentTarget as HTMLElement;
		target.setPointerCapture?.(event.pointerId);
		const anchor = timeAt(event.clientX);
		const originX = event.clientX;
		let moved = false;
		const move = (next: PointerEvent) => {
			if (!moved && Math.abs(next.clientX - originX) < 4) return;
			moved = true;
			onrange?.(fromDrag(anchor, timeAt(next.clientX), duration));
		};
		const up = () => {
			if (!moved) onseek(anchor);
			target.removeEventListener('pointermove', move);
			target.removeEventListener('pointerup', up);
			target.removeEventListener('pointercancel', up);
		};
		target.addEventListener('pointermove', move);
		target.addEventListener('pointerup', up);
		target.addEventListener('pointercancel', up);
	}

	function handleKey(event: KeyboardEvent, which: 'in' | 'out' | 'range-start' | 'range-end') {
		const step = event.shiftKey ? 1 : frameStep;
		const value = which === 'in' ? inPoint : which === 'out' ? outPoint : which === 'range-start' ? (range?.start ?? 0) : (range?.end ?? duration);
		let next: number | null = null;
		if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next = value - step;
		else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next = value + step;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = duration;
		if (next === null) return;
		event.preventDefault();
		event.stopPropagation();
		const clamped = Math.max(0, Math.min(duration, next));
		if (which === 'range-start') onrangestart?.(clamped);
		else if (which === 'range-end') onrangeend?.(clamped);
		else (which === 'in' ? onin : onout)(clamped);
	}

	function onkeydown(event: KeyboardEvent) {
		if (!active || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
		if (!pageOwnsKeys(event)) return;
		if (event.target instanceof Element && event.target.closest('[data-scrub-handle]')) return;
		const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
		if (key === 'k') ontoggleplay();
		else if (key === 'i') (rangeMode ? (onrangestart ?? onin) : onin)(playhead);
		else if (key === 'o') (rangeMode ? (onrangeend ?? onout) : onout)(playhead);
		else if (key === 'ArrowLeft' && !event.shiftKey) onstep(-1);
		else if (key === 'ArrowRight' && !event.shiftKey) onstep(1);
		else return;
		event.preventDefault();
		event.stopPropagation();
	}
</script>

<svelte:window onkeydowncapture={onkeydown} />

<div
	class="flex flex-none flex-col border-t border-line bg-surface-1 {compact ? 'gap-2 px-3 pb-2.5 pt-2.5' : 'gap-1.5 px-4 pb-3 pt-2.5'}"
	role="group"
	aria-label="Scrub bar"
	data-scrub-bar={compact ? 'compact' : 'full'}
>
	<div class="flex min-w-0 items-center gap-1.5">
		<Tooltip text={playing ? 'Pause' : 'Play'} kbd="K" position="top">
			<button
				type="button"
				class="grid h-8 w-8 flex-none place-items-center rounded bg-accent text-accent-contrast hover:bg-accent-hover focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal"
				aria-label={playing ? 'Pause' : 'Play'}
				aria-pressed={playing}
				onclick={ontoggleplay}
				data-scrub-play
			>
				<svg viewBox="0 0 16 16" class="h-4 w-4" aria-hidden="true">
					{#if playing}
						<path d="M4.5 3h2.5v10H4.5zM9 3h2.5v10H9z" fill="currentColor" />
					{:else}
						<path d="M5 2.8v10.4L13 8z" fill="currentColor" />
					{/if}
				</svg>
			</button>
		</Tooltip>
		{#if !compact}
			<IconButton icon="chevron-left" label="Previous frame" size="sm" onclick={() => onstep(-1)} data-scrub-prev />
			<IconButton icon="chevron-right" label="Next frame" size="sm" onclick={() => onstep(1)} data-scrub-next />
		{/if}
		<span class="ml-1.5 whitespace-nowrap font-mono text-sm tabular-nums" data-scrub-time>
			<span class="text-fg">{formatEditorTime(playhead)}</span>
			<span class="text-fg-subtle"> / {formatEditorTime(duration)}</span>
		</span>
		{#if !compact}
			<span class="ml-1"></span>
			<IconButton icon="refresh" label={loop ? 'Loop in to out: on' : 'Loop in to out: off'} size="sm" active={loop} ariaPressed={loop} onclick={() => ontoggleloop?.()} data-scrub-loop />
			<IconButton icon={muted ? 'editor-mute' : 'editor-volume'} label={muted ? 'Unmute playback' : 'Mute playback'} size="sm" active={muted} ariaPressed={muted} onclick={() => ontogglemute?.()} data-scrub-mute />
			<span class="flex-1"></span>
			<span class="flex items-center gap-4 whitespace-nowrap font-mono text-sm uppercase tabular-nums text-fg-subtle" data-scrub-readouts>
				{#if rangeMode}
					<span class="text-signal" data-scrub-section>{rangeName} <span data-scrub-section-times>{range ? `${formatEditorTime(range.start)} → ${formatEditorTime(range.end)}` : 'Whole clip'}</span></span>
				{:else}
					<span>In <span class="text-fg" data-scrub-in>{formatEditorTime(inPoint)}</span></span>
					<span>Out <span class="text-fg" data-scrub-out>{formatEditorTime(outPoint)}</span></span>
				{/if}
				<span>Length <span class="normal-case text-fg" data-scrub-length>{fixed2(rangeMode ? (range ? range.end - range.start : duration) : outputLength)} s</span></span>
			</span>
		{:else}
			<span class="flex-1"></span>
			<span class="whitespace-nowrap font-mono text-sm tabular-nums text-fg-muted" data-scrub-length>{fixed2(outputLength)} s</span>
		{/if}
	</div>

	{#if !compact}
		<div class="relative h-[18px]" aria-hidden="true" data-scrub-ruler>
			{#each ticks as tick (tick)}
				<span class="absolute bottom-0 h-1.5 w-px bg-line-strong" style="left: {pct(tick)}%"></span>
				<span class="absolute top-0 -translate-x-1/2 font-mono text-sm leading-none text-fg-subtle {tick === 0 ? 'translate-x-0' : ''}" style="left: {pct(tick)}%">{tick}s</span>
			{/each}
		</div>
	{/if}

	<div
		bind:this={track}
		class="relative h-12 cursor-pointer touch-none select-none overflow-visible rounded bg-surface-2"
		role="slider"
		tabindex="0"
		aria-label="Playhead"
		aria-valuemin={0}
		aria-valuemax={Math.round(duration * 100) / 100}
		aria-valuenow={Math.round(playhead * 100) / 100}
		aria-valuetext={formatEditorTime(playhead)}
		onpointerdown={(event) => (rangeMode ? markDrag(event) : drag(event, onseek))}
		data-scrub-track
	>
		<div class="absolute inset-0 flex overflow-hidden rounded" aria-hidden="true" bind:clientWidth={stripWidth} data-scrub-strip>
			{#each frames as frame, i (i)}
				<div class="h-full overflow-hidden bg-surface-3 {tileWidth > 0 ? 'flex-none' : 'min-w-0 flex-1'}" style={tileWidth > 0 ? `width: ${tileWidth}px` : undefined}>
					{#if frame}<img src={frame} alt="" class="h-full w-full" draggable="false" data-scrub-frame />{/if}
				</div>
			{/each}
		</div>
		{#if trimmed}
			<div class="pointer-events-none absolute inset-y-0 left-0 rounded-l bg-scrim/60" style="width: {inPct}%" data-scrub-dim="in"></div>
			<div class="pointer-events-none absolute inset-y-0 right-0 rounded-r bg-scrim/60" style="width: {100 - outPct}%" data-scrub-dim="out"></div>
		{/if}
		{#each markers as marker (marker.label)}
			<div class="pointer-events-none absolute inset-y-0 w-px bg-scrim-on" style="left: {pct(marker.at)}%" data-scrub-marker={marker.label}>
				<span class="absolute bottom-0.5 left-1/2 -translate-x-1/2 rounded-sm bg-scrim px-1 font-mono text-sm leading-4 text-scrim-on">{marker.label}</span>
			</div>
		{/each}
		{#if rangeMode && rangeMarker}
			<div class="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-signal" style="left: {pct(rangeMarker.at)}%" data-scrub-extend-marker>
				<span class="absolute -top-5 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-sm bg-signal-solid px-1.5 font-mono text-sm leading-4 text-white">{rangeMarker.label}</span>
			</div>
		{/if}
		{#if rangeMode && range}
			<div class="pointer-events-none absolute -inset-y-px rounded border-2 border-signal bg-signal/10" style="left: {rangeStartPct}%; width: {Math.max(0, rangeEndPct - rangeStartPct)}%" data-scrub-section-box>
				<span class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-sm bg-scrim/80 px-2 font-mono text-sm leading-5 tabular-nums text-scrim-on">{rangeName} · {fixed2(range.end - range.start)} s</span>
			</div>
			<div
				class="absolute -inset-y-px -ml-2 flex w-3 cursor-ew-resize items-center justify-center rounded-l bg-signal-solid focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal"
				style="left: {rangeStartPct}%"
				role="slider"
				tabindex="0"
				aria-label="Range start"
				aria-valuemin={0}
				aria-valuemax={Math.round(duration * 100) / 100}
				aria-valuenow={Math.round(range.start * 100) / 100}
				aria-valuetext={formatEditorTime(range.start)}
				onpointerdown={(event) => drag(event, (seconds) => onrangestart?.(seconds))}
				onkeydown={(event) => handleKey(event, 'range-start')}
				data-scrub-handle="range-start"
			>
				<span class="h-4 w-0.5 rounded-sm bg-white/80"></span>
			</div>
			<div
				class="absolute -inset-y-px -mr-2 flex w-3 cursor-ew-resize items-center justify-center rounded-r bg-signal-solid focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal"
				style="right: {100 - rangeEndPct}%"
				role="slider"
				tabindex="0"
				aria-label="Range end"
				aria-valuemin={0}
				aria-valuemax={Math.round(duration * 100) / 100}
				aria-valuenow={Math.round(range.end * 100) / 100}
				aria-valuetext={formatEditorTime(range.end)}
				onpointerdown={(event) => drag(event, (seconds) => onrangeend?.(seconds))}
				onkeydown={(event) => handleKey(event, 'range-end')}
				data-scrub-handle="range-end"
			>
				<span class="h-4 w-0.5 rounded-sm bg-white/80"></span>
			</div>
		{/if}
		{#if handles}
			<div class="pointer-events-none absolute -inset-y-px rounded border-2 border-scrim-on" style="left: {inPct}%; width: {Math.max(0, outPct - inPct)}%" data-scrub-range></div>
			<div
				class="absolute -inset-y-px -ml-2 flex w-3 cursor-ew-resize items-center justify-center rounded-l bg-scrim-on focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal"
				style="left: {inPct}%"
				role="slider"
				tabindex="0"
				aria-label="In point"
				aria-valuemin={0}
				aria-valuemax={Math.round(duration * 100) / 100}
				aria-valuenow={Math.round(inPoint * 100) / 100}
				aria-valuetext={formatEditorTime(inPoint)}
				onpointerdown={(event) => drag(event, onin)}
				onkeydown={(event) => handleKey(event, 'in')}
				data-scrub-handle="in"
			>
				<span class="h-4 w-0.5 rounded-sm bg-scrim/60"></span>
			</div>
			<div
				class="absolute -inset-y-px -mr-2 flex w-3 cursor-ew-resize items-center justify-center rounded-r bg-scrim-on focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal"
				style="right: {100 - outPct}%"
				role="slider"
				tabindex="0"
				aria-label="Out point"
				aria-valuemin={0}
				aria-valuemax={Math.round(duration * 100) / 100}
				aria-valuenow={Math.round(outPoint * 100) / 100}
				aria-valuetext={formatEditorTime(outPoint)}
				onpointerdown={(event) => drag(event, onout)}
				onkeydown={(event) => handleKey(event, 'out')}
				data-scrub-handle="out"
			>
				<span class="h-4 w-0.5 rounded-sm bg-scrim/60"></span>
			</div>
		{/if}
		<div class="pointer-events-none absolute -top-2 bottom-0 w-0.5 -translate-x-1/2 bg-signal" style="left: {headPct}%" data-scrub-playhead>
			<span class="absolute -top-0.5 left-1/2 h-0 w-0 -translate-x-1/2 border-x-[5px] border-t-[6px] border-x-transparent border-t-signal"></span>
		</div>
	</div>

	{#if !compact && !rangeMode}
		<div class="relative h-5 overflow-hidden rounded bg-surface-2" aria-hidden="true" data-scrub-wave={peaks ? 'ready' : 'empty'}>
			{#if peaks && peaks.length}
				<svg class="absolute inset-0 h-full w-full" viewBox="0 0 {peaks.length} 20" preserveAspectRatio="none">
					{#each peaks as peak, i (i)}
						{@const h = Math.max(1.5, Math.min(1, peak) * 18)}
						{@const inside = ((i + 0.5) / peaks.length) * 100 >= inPct && ((i + 0.5) / peaks.length) * 100 <= outPct}
						<rect x={i + 0.15} y={(20 - h) / 2} width="0.7" height={h} class={inside ? 'fill-fg-muted' : 'fill-fg-subtle opacity-40'} />
					{/each}
				</svg>
			{/if}
			{#if fadeIn > 0}
				<div class="pointer-events-none absolute inset-y-0 bg-signal/30" style="left: {inPct}%; width: {Math.min(outPct - inPct, pct(fadeIn))}%; clip-path: polygon(0 100%, 100% 0, 100% 100%)" data-scrub-fade="in"></div>
			{/if}
			{#if fadeOut > 0}
				<div class="pointer-events-none absolute inset-y-0 bg-signal/30" style="right: {100 - outPct}%; width: {Math.min(outPct - inPct, pct(fadeOut))}%; clip-path: polygon(0 0, 100% 100%, 0 100%)" data-scrub-fade="out"></div>
			{/if}
			{#if replacement}
				<span class="pointer-events-none absolute inset-y-0 left-1 flex max-w-full items-center truncate rounded-sm bg-surface-2/90 px-1.5 font-mono text-sm text-signal" data-scrub-track-name>{replacement.name}</span>
			{/if}
		</div>
	{/if}
</div>
