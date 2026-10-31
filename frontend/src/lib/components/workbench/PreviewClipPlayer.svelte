<script lang="ts">
	import { onMount } from 'svelte';
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import {
		adminFormatChip,
		chipLabel,
		clipCadence,
		frameOrigin,
		framesToAdvance,
		isBehind,
		lastFrameIndex,
		wrapFrameIndex,
		type PreviewClip
	} from '$lib/generation/previewClip';

	let {
		clip,
		progress = 0,
		liveStep = 0,
		isAdmin = false,
		ruler = true
	}: {
		clip: PreviewClip;
		progress?: number;
		liveStep?: number;
		isAdmin?: boolean;
		ruler?: boolean;
	} = $props();

	let canvas: HTMLCanvasElement | undefined = $state();
	let paused = $state(false);
	let hovered = false;
	let reducedMotion = $state(false);
	let sprite: HTMLImageElement | null = null;
	let spriteSource = '';
	let frameIndex = 0;
	let lastTick = 0;
	let carry = 0;
	let raf = 0;

	let behind = $derived(isBehind(clip.step, liveStep, clipCadence(clip.total_steps)));
	let fillPercent = $derived(Math.round(Math.min(1, Math.max(0, progress)) * 100));

	function draw() {
		if (!canvas || !sprite || !sprite.complete || sprite.naturalWidth === 0) return;
		const context = canvas.getContext('2d');
		if (!context) return;
		if (canvas.width !== clip.frame_width) canvas.width = clip.frame_width;
		if (canvas.height !== clip.frame_height) canvas.height = clip.frame_height;
		const index = reducedMotion ? lastFrameIndex(clip.frames) : wrapFrameIndex(frameIndex, clip.frames);
		const { sx, sy } = frameOrigin(clip, index);
		context.imageSmoothingEnabled = true;
		context.imageSmoothingQuality = 'high';
		context.drawImage(sprite, sx, sy, clip.frame_width, clip.frame_height, 0, 0, canvas.width, canvas.height);
	}

	function loadSprite(source: string) {
		if (source === spriteSource) return;
		spriteSource = source;
		const image = new Image();
		image.onload = () => {
			if (spriteSource !== source) return;
			sprite = image;
			draw();
		};
		image.src = source;
	}

	function tick(now: number) {
		raf = requestAnimationFrame(tick);
		if (paused || reducedMotion || !sprite) {
			lastTick = now;
			return;
		}
		carry += now - lastTick;
		lastTick = now;
		const steps = framesToAdvance(carry, clip.fps);
		if (steps > 0) {
			carry -= (steps * 1000) / clip.fps;
			frameIndex = wrapFrameIndex(frameIndex + steps, clip.frames);
			draw();
		}
	}

	$effect(() => {
		loadSprite(clip.image);
	});

	$effect(() => {
		reducedMotion;
		clip.frames;
		draw();
	});

	onMount(() => {
		const query = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
		const onChange = (event: MediaQueryListEvent) => (reducedMotion = event.matches);
		reducedMotion = query?.matches ?? false;
		query?.addEventListener?.('change', onChange);
		lastTick = performance.now();
		raf = requestAnimationFrame(tick);
		return () => {
			cancelAnimationFrame(raf);
			query?.removeEventListener?.('change', onChange);
		};
	});

	function toggle() {
		paused = !paused;
	}

	function onKeydown(event: KeyboardEvent) {
		if (!hovered || reducedMotion) return;
		const target = event.target as HTMLElement | null;
		if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName))) return;
		if (event.key === ' ' || event.code === 'Space') {
			event.preventDefault();
			toggle();
		}
	}
</script>

<svelte:window onkeydown={onKeydown} />

<div
	class="group/clip absolute inset-0 overflow-hidden"
	data-preview-clip
	data-paused={paused}
	data-reduced={reducedMotion}
	role="group"
	aria-label="Live preview"
	onmouseenter={() => (hovered = true)}
	onmouseleave={() => (hovered = false)}
>
	<canvas bind:this={canvas} class="h-full w-full object-contain" style="image-rendering: auto" aria-hidden="true"></canvas>

	<div class="pointer-events-none absolute left-3 top-3 z-20 flex items-center gap-2" data-preview-chip>
		<span
			class="inline-flex items-center gap-1.5 rounded border border-white/20 bg-black/70 px-2 py-1 font-mono text-xs font-medium uppercase tracking-[0.07em] text-white tabular-nums"
		>
			<span class="h-1.5 w-1.5 rounded-full {behind ? 'bg-warning-solid' : 'bg-signal-solid'}" data-preview-dot></span>
			{chipLabel(clip.step)}
		</span>
	</div>

	{#if isAdmin && clip.admin_detail}
		<span
			class="pointer-events-none absolute right-3 top-3 z-20 rounded border border-white/20 bg-black/70 px-2 py-1 font-mono text-xs tabular-nums text-white"
			data-preview-format
		>
			{adminFormatChip(clip)}
		</span>
	{/if}

	{#if !reducedMotion}
		<div
			class="absolute bottom-16 right-4 z-20 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover/clip:opacity-100 [@media(hover:none)]:opacity-100"
			data-preview-control
		>
			<IconButton
				icon={paused ? 'play' : 'pause'}
				label={paused ? 'Resume preview' : 'Pause preview'}
				variant="overlay"
				size="sm"
				ariaPressed={paused}
				onclick={toggle}
			/>
		</div>
	{/if}

	{#if ruler}
		<div class="absolute inset-x-0 bottom-0 z-30" data-preview-ruler>
			<div class="tick-track">
				<div class="h-full bg-signal-solid transition-all duration-300" style="width: {fillPercent}%"></div>
			</div>
		</div>
	{/if}
</div>

<style>
	.tick-track {
		position: relative;
		height: 4px;
		background: rgb(0 0 0 / 0.35);
		overflow: hidden;
	}
	.tick-track::after {
		content: '';
		position: absolute;
		inset: 0;
		background: repeating-linear-gradient(
			90deg,
			transparent 0,
			transparent 7px,
			rgb(0 0 0 / 0.45) 7px,
			rgb(0 0 0 / 0.45) 8px
		);
		pointer-events: none;
	}
</style>
