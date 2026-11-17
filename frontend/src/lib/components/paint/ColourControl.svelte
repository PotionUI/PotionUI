<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import dismiss from '$lib/actions/dismiss';
	import { hexToHsv, hsvToHex, normalizeHex, type Hsv } from '$lib/paint/colourMath';
	import { SWATCHES } from '$lib/paint/palette';
	import { RECENT_SHOWN, recentColours, rememberColour, subscribeRecentColours } from '$lib/paint/recentColours';
	import { trackMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';

	let { color, onchange }: { color: string; onchange: (hex: string) => void } = $props();

	const WIDTH = 288;
	const REMEMBER_MS = 700;

	let open = $state(false);
	let trigger = $state<HTMLButtonElement>();
	let panel = $state<HTMLElement>();
	let pad = $state<HTMLElement>();
	let position = $state<FlippedMenuPosition | null>(null);
	let recent = $state<string[]>(recentColours());
	let hsv = $state<Hsv>(untrack(() => hexToHsv(color)));
	let draft = $state<string | null>(null);
	let timer: ReturnType<typeof setTimeout> | null = null;

	const stopRecent = subscribeRecentColours(() => (recent = recentColours()));

	onDestroy(() => {
		stopRecent();
		if (timer) clearTimeout(timer);
	});

	let shownRecent = $derived(recent.filter((value) => value !== color.toLowerCase()).slice(0, RECENT_SHOWN));
	let hueColour = $derived(hsvToHex({ h: hsv.h, s: 1, v: 1 }));

	$effect(() => {
		const next = color;
		untrack(() => {
			if (hsvToHex(hsv).toLowerCase() === next.toLowerCase()) return;
			const parsed = hexToHsv(next);
			hsv = { ...parsed, h: parsed.s === 0 ? hsv.h : parsed.h };
		});
	});

	$effect(() => {
		const current = color;
		if (timer) clearTimeout(timer);
		timer = setTimeout(() => rememberColour(current), REMEMBER_MS);
		return () => {
			if (timer) clearTimeout(timer);
		};
	});

	$effect(() => {
		if (!open || !trigger || !panel) return;
		const node = panel;
		return trackMenuPosition(trigger, () => ({ width: WIDTH, heightEstimate: node.offsetHeight, gap: 6, align: 'left', preferred: 'down' }), (next) => (position = next));
	});

	function style(pos: FlippedMenuPosition | null): string {
		if (!pos) return `visibility: hidden; top: 0; left: 0; width: ${WIDTH}px;`;
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `${vertical} left: ${pos.left}px; width: ${WIDTH}px; max-height: ${pos.maxHeight}px;`;
	}

	function close(refocus: boolean) {
		open = false;
		position = null;
		draft = null;
		if (refocus) queueMicrotask(() => trigger?.focus());
	}

	function pick(hex: string) {
		draft = null;
		onchange(hex.toLowerCase());
	}

	function setHsv(next: Hsv) {
		hsv = next;
		onchange(hsvToHex(next));
	}

	function fromPoint(event: PointerEvent) {
		if (!pad) return;
		const box = pad.getBoundingClientRect();
		const s = Math.min(1, Math.max(0, (event.clientX - box.left) / Math.max(1, box.width)));
		const v = 1 - Math.min(1, Math.max(0, (event.clientY - box.top) / Math.max(1, box.height)));
		setHsv({ ...hsv, s, v });
	}

	function padDown(event: PointerEvent) {
		pad?.setPointerCapture(event.pointerId);
		fromPoint(event);
	}

	function padMove(event: PointerEvent) {
		if (pad?.hasPointerCapture(event.pointerId)) fromPoint(event);
	}

	function padKey(event: KeyboardEvent) {
		const step = event.shiftKey ? 0.1 : 0.02;
		const delta: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
		const move = delta[event.key];
		if (!move) return;
		event.preventDefault();
		setHsv({ ...hsv, s: Math.min(1, Math.max(0, hsv.s + move[0])), v: Math.min(1, Math.max(0, hsv.v + move[1])) });
	}

	function hueInput(event: Event) {
		setHsv({ ...hsv, h: Number((event.currentTarget as HTMLInputElement).value) });
	}

	function hexInput(event: Event) {
		draft = (event.currentTarget as HTMLInputElement).value;
		const hex = normalizeHex(draft);
		if (hex) onchange(hex);
	}

	function hexCommit() {
		draft = null;
	}

	function onkeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			close(true);
		}
	}
</script>

<div class="flex min-w-0 items-center gap-2.5" data-paint-colour>
	<button
		bind:this={trigger}
		type="button"
		class="h-8 w-10 flex-none rounded border border-line-strong focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-2 focus-visible:outline-signal {open ? 'ring-1 ring-signal' : ''}"
		style="background: {color}"
		aria-label="Colour {color}"
		aria-haspopup="dialog"
		aria-expanded={open}
		data-colour-swatch
		onclick={() => (open ? close(false) : (open = true))}
	></button>
	<span class="font-mono text-sm uppercase tabular-nums text-fg-muted" data-colour-hex>{color}</span>
	<span class="flex-1"></span>
	<div class="flex items-center gap-1.5" data-colour-recent>
		{#each shownRecent as value (value)}
			<button
				type="button"
				class="h-6 w-6 flex-none rounded border border-line-strong focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal"
				style="background: {value}"
				aria-label="Use recent colour {value}"
				onclick={() => pick(value)}
			></button>
		{/each}
	</div>
</div>

{#if open}
	<div
		bind:this={panel}
		use:portal
		use:overlayLayer
		use:dismiss={{ onDismiss: () => close(false), ignore: [trigger] }}
		role="dialog"
		aria-label="Colour picker"
		tabindex="-1"
		class="fixed flex flex-col gap-3 overflow-y-auto rounded-xl border border-line-strong bg-surface-2 p-3 shadow-overlay"
		style={style(position)}
		{onkeydown}
		data-colour-picker
	>
		<div
			bind:this={pad}
			class="relative h-36 w-full flex-none cursor-crosshair touch-none rounded"
			style="background: linear-gradient(to top, rgb(0 0 0), rgb(0 0 0 / 0)), linear-gradient(to right, rgb(255 255 255), {hueColour})"
			role="slider"
			tabindex="0"
			aria-label="Saturation and brightness"
			aria-valuemin="0"
			aria-valuemax="100"
			aria-valuenow={Math.round(hsv.v * 100)}
			aria-valuetext="Saturation {Math.round(hsv.s * 100)}%, brightness {Math.round(hsv.v * 100)}%"
			onpointerdown={padDown}
			onpointermove={padMove}
			onkeydown={padKey}
			data-colour-pad
		>
			<span
				class="pointer-events-none absolute h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-fg shadow-raised"
				style="left: {hsv.s * 100}%; top: {(1 - hsv.v) * 100}%; background: {color}"
				aria-hidden="true"
			></span>
		</div>
		<input
			type="range"
			min="0"
			max="360"
			step="1"
			value={Math.round(hsv.h)}
			aria-label="Hue"
			class="hue-range h-3 w-full cursor-pointer appearance-none rounded-full"
			oninput={hueInput}
			data-colour-hue
		/>
		<div class="flex items-center gap-2">
			<span class="h-8 w-8 flex-none rounded border border-line-strong" style="background: {color}" aria-hidden="true"></span>
			<input
				type="text"
				value={draft ?? color.toUpperCase()}
				aria-label="Hex colour"
				spellcheck="false"
				autocomplete="off"
				class="h-8 min-w-0 flex-1 rounded border border-line-strong bg-surface-1 px-2 font-mono text-sm uppercase tabular-nums text-fg focus:border-signal/60 focus:outline-none"
				oninput={hexInput}
				onblur={hexCommit}
				data-colour-hex-input
			/>
		</div>
		<div class="flex flex-col gap-1.5">
			<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Swatches</span>
			<div class="flex flex-wrap gap-1.5" data-colour-swatches>
				{#each SWATCHES as swatch (swatch)}
					<button
						type="button"
						class="h-6 w-6 flex-none rounded border border-line-strong focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal {color.toLowerCase() === swatch ? 'ring-2 ring-signal' : ''}"
						style="background: {swatch}"
						aria-label="Use colour {swatch}"
						aria-pressed={color.toLowerCase() === swatch}
						onclick={() => pick(swatch)}
					></button>
				{/each}
			</div>
		</div>
		{#if shownRecent.length}
			<div class="flex flex-col gap-1.5">
				<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Recent</span>
				<div class="flex flex-wrap gap-1.5" data-colour-recent-list>
					{#each shownRecent as value (value)}
						<button
							type="button"
							class="h-6 w-6 flex-none rounded border border-line-strong focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal"
							style="background: {value}"
							aria-label="Use recent colour {value}"
							onclick={() => pick(value)}
						></button>
					{/each}
				</div>
			</div>
		{/if}
		<p class="text-sm text-fg-subtle">Alt+click on the picture picks a colour.</p>
	</div>
{/if}

<style>
	.hue-range {
		background: linear-gradient(to right, hsl(0 100% 50%), hsl(60 100% 50%), hsl(120 100% 50%), hsl(180 100% 50%), hsl(240 100% 50%), hsl(300 100% 50%), hsl(360 100% 50%));
	}

	.hue-range::-webkit-slider-thumb {
		appearance: none;
		width: 14px;
		height: 14px;
		border-radius: 50%;
		border: 2px solid rgb(var(--fg));
		background: rgb(var(--surface-2));
		cursor: pointer;
	}

	.hue-range::-moz-range-thumb {
		width: 10px;
		height: 10px;
		border-radius: 50%;
		border: 2px solid rgb(var(--fg));
		background: rgb(var(--surface-2));
		cursor: pointer;
	}
</style>
