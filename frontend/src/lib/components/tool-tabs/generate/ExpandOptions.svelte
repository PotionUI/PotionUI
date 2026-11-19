<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { EditorToolProps } from '$lib/editors/railTools';
	import { api } from '$lib/services/api/index';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import {
		CUSTOM_ID,
		MAX_PICKS,
		anchorFor,
		atLeast,
		expandUi,
		formatName,
		parseRatio,
		passesFor,
		placement,
		targetsOf,
		togglePick,
		type Anchor
	} from '$lib/tool-tabs/image/generate/expand';

	let { host }: EditorToolProps = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;

	let targets = $derived(targetsOf($expandUi));
	let source = $derived($expandUi.source);
	let lead = $derived(targets[0] ?? null);
	let passes = $derived(lead && source ? passesFor(source, lead) : 0);
	let thumb = $state<string | null>(null);
	let box = $state<HTMLElement | null>(null);
	let drag = $state<{ pointer: number; startX: number; startY: number; from: { x: number; y: number } } | null>(null);
	let failed = $state(false);

	const sizes = $derived.by(() => {
		const found = new Map<string, string>();
		if (!source) return found;
		for (const format of $expandUi.formats) {
			const ratio = parseRatio(format.ratio);
			const target = ratio ? targetsOf({ ...$expandUi, picks: [format.id] }).at(0) : null;
			if (target) found.set(format.id, `${target.width}×${target.height}`);
		}
		const custom = atLeast(source, $expandUi.custom);
		found.set(CUSTOM_ID, `${custom.width}×${custom.height}`);
		return found;
	});

	onMount(() => {
		if ($expandUi.loaded) return;
		void api
			.listExpandFormats()
			.then((formats) => expandUi.update((current) => ({ ...current, formats, loaded: true })))
			.catch(() => (failed = true));
	});

	$effect(() => {
		const revision = source ? `${source.width}x${source.height}` : '';
		void revision;
		let alive = true;
		void host
			.render()
			.then((canvas) => {
				if (!alive) return;
				const scale = 160 / Math.max(canvas.width, canvas.height);
				const small = document.createElement('canvas');
				small.width = Math.max(1, Math.round(canvas.width * scale));
				small.height = Math.max(1, Math.round(canvas.height * scale));
				small.getContext('2d')?.drawImage(canvas, 0, 0, small.width, small.height);
				thumb = small.toDataURL('image/jpeg', 0.7);
			})
			.catch(() => (thumb = null));
		return () => {
			alive = false;
		};
	});

	function pick(id: string) {
		expandUi.update((current) => togglePick(current, id));
	}

	function setCustom(side: 'width' | 'height', raw: string) {
		const value = Math.round(Number(raw));
		if (!Number.isFinite(value) || value < 1) return;
		expandUi.update((current) => ({ ...current, custom: { ...current.custom, [side]: Math.min(8192, value) } }));
	}

	function move(anchor: Anchor) {
		expandUi.update((current) => ({ ...current, anchor }));
	}

	function onDown(event: PointerEvent) {
		if (!lead || !source) return;
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		drag = { pointer: event.pointerId, startX: event.clientX, startY: event.clientY, from: { x: lead.x, y: lead.y } };
	}

	function onMove(event: PointerEvent) {
		if (!drag || !lead || !source || !box) return;
		const scale = box.getBoundingClientRect().width / lead.width;
		if (!scale) return;
		const offset = { x: drag.from.x + (event.clientX - drag.startX) / scale, y: drag.from.y + (event.clientY - drag.startY) / scale };
		move(anchorFor(source, lead, offset));
	}

	function onUp() {
		drag = null;
	}

	function onKey(event: KeyboardEvent) {
		if (!lead || !source) return;
		const step = event.shiftKey ? 0.1 : 0.02;
		const delta: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
		const change = delta[event.key];
		if (!change) return;
		event.preventDefault();
		const anchor = $expandUi.anchor;
		move({ x: Math.min(1, Math.max(0, anchor.x + change[0])), y: Math.min(1, Math.max(0, anchor.y + change[1])) });
	}

	function recenter() {
		move({ x: 0.5, y: 0.5 });
	}

	const row = 'flex h-8 w-full items-center gap-2 rounded border px-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal';
	const idle = 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg';
	const on = 'border-signal bg-surface-3 text-fg';
	let placed = $derived(lead && source ? placement(source, lead, lead.anchor) : null);
</script>

<div class="flex flex-col gap-3" data-expand-options>
	<div class="flex flex-col gap-1" role="group" aria-label="Formats">
		<div class="flex items-center justify-between">
			<span class="text-sm font-medium text-fg">Format</span>
			<span class="font-mono text-sm tabular-nums text-fg-subtle" data-expand-count>{targets.length} of {MAX_PICKS}</span>
		</div>
		{#if failed}
			<p class="text-sm text-danger" role="alert">The formats could not be loaded.</p>
		{/if}
		{#each $expandUi.formats as format (format.id)}
			{@const chosen = $expandUi.picks.includes(format.id)}
			<button type="button" class="{row} {chosen ? on : idle}" aria-pressed={chosen} data-expand-format={format.id} onclick={() => pick(format.id)}>
				<span class="flex h-4 w-4 flex-none items-center justify-center rounded border {chosen ? 'border-signal text-signal' : 'border-line-strong'}">
					{#if chosen}<Icon name="check" className="h-3 w-3" />{/if}
				</span>
				<span class="min-w-0 flex-1 truncate">{format.label}</span>
				<span class="flex-none font-mono tabular-nums text-fg-subtle">{format.ratio}</span>
				<span class="w-20 flex-none text-right font-mono tabular-nums text-fg-subtle">{sizes.get(format.id) ?? ''}</span>
			</button>
		{/each}
		<button type="button" class="{row} {$expandUi.picks.includes(CUSTOM_ID) ? on : idle}" aria-pressed={$expandUi.picks.includes(CUSTOM_ID)} data-expand-format={CUSTOM_ID} onclick={() => pick(CUSTOM_ID)}>
			<span class="flex h-4 w-4 flex-none items-center justify-center rounded border {$expandUi.picks.includes(CUSTOM_ID) ? 'border-signal text-signal' : 'border-line-strong'}">
				{#if $expandUi.picks.includes(CUSTOM_ID)}<Icon name="check" className="h-3 w-3" />{/if}
			</span>
			<span class="min-w-0 flex-1 truncate">Custom</span>
			<span class="w-20 flex-none text-right font-mono tabular-nums text-fg-subtle">{sizes.get(CUSTOM_ID) ?? ''}</span>
		</button>
		{#if $expandUi.picks.includes(CUSTOM_ID)}
			<div class="flex items-center gap-2" data-expand-custom>
				<input aria-label="Width" type="number" min="1" max="8192" class="input h-8 w-full font-mono tabular-nums" value={$expandUi.custom.width} onchange={(event) => setCustom('width', event.currentTarget.value)} />
				<span class="text-fg-subtle">×</span>
				<input aria-label="Height" type="number" min="1" max="8192" class="input h-8 w-full font-mono tabular-nums" value={$expandUi.custom.height} onchange={(event) => setCustom('height', event.currentTarget.value)} />
			</div>
		{/if}
	</div>

	{#if lead && source}
		<div class="flex flex-col gap-1.5">
			<div class="flex items-center justify-between">
				<span class="text-sm font-medium text-fg">Position</span>
				<button type="button" class="text-sm text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal" onclick={recenter} data-expand-center>Center</button>
			</div>
			<div class="flex justify-center rounded border border-line bg-canvas p-1.5">
				<div bind:this={box} class="relative w-full overflow-hidden rounded border border-line-strong bg-surface-2" style="aspect-ratio: {lead.width} / {lead.height}; width: min(100%, calc(9rem * {lead.width} / {lead.height}))" data-expand-frame>
					{#if placed}
						<div
							class="absolute cursor-grab touch-none overflow-hidden rounded-sm border border-signal bg-surface-3 shadow-raised focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal {drag ? 'cursor-grabbing' : ''}"
							style="left: {(placed.x / lead.width) * 100}%; top: {(placed.y / lead.height) * 100}%; width: {(source.width / lead.width) * 100}%; height: {(source.height / lead.height) * 100}%"
							role="button"
							tabindex="0"
							aria-label="Move the picture. Drag it, or use the arrow keys"
							data-expand-picture
							onpointerdown={onDown}
							onpointermove={onMove}
							onpointerup={onUp}
							onpointercancel={onUp}
							onkeydown={onKey}
						>
							{#if thumb}<img src={thumb} alt="" class="h-full w-full select-none object-cover" draggable="false" />{/if}
						</div>
					{/if}
				</div>
			</div>
			<p class="text-sm text-fg-muted">
				{#if targets.length > 1}
					The picture sits the same way in every format. The first one is shown.
				{:else}
					Drag the picture to choose where the new area goes.
				{/if}
			</p>
			{#if passes > 1}
				<p class="text-sm text-fg-subtle" data-expand-passes>{formatName(lead)} is made in {passes} passes.</p>
			{/if}
		</div>
	{:else if !$expandUi.picks.length}
		<p class="text-sm text-fg-muted" data-expand-hint>Choose one or more formats. The picture stays as it is and the new area is generated around it.</p>
	{/if}

	{#if $ui.spawned.length}
		<p class="text-sm text-fg-muted" role="status" data-expand-spawned>
			Also started {$ui.spawned.length === 1 ? 'one more edit' : `${$ui.spawned.length} more edits`}: {$ui.spawned.join(', ')}. Open {$ui.spawned.length === 1 ? 'it' : 'them'} from Recent edits.
		</p>
	{/if}
</div>
