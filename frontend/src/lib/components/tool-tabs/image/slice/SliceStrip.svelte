<script lang="ts" module>
	export interface StripItem {
		id: string;
		n: string;
		thumb: string | null;
		width: number;
		height: number;
		state?: 'done' | 'saving' | 'queued' | null;
	}
</script>

<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import Icon from '$lib/components/Icon.svelte';

	let {
		items,
		skipped = [],
		phone = false,
		locked = false,
		destination = 'Library',
		onrestore,
		onreorder
	}: {
		items: StripItem[];
		skipped?: { id: string; thumb: string | null }[];
		phone?: boolean;
		locked?: boolean;
		destination?: string;
		onrestore: (id: string) => void;
		onreorder: (id: string, to: number) => void;
	} = $props();

	let lifted = $state<string | null>(null);
	let over = $state<number | null>(null);
	let h = $derived(phone ? 52 : 68);
	let saving = $derived(items.some((item) => item.state && item.state !== 'done'));
	let saved = $derived(items.length > 0 && items.every((item) => item.state === 'done'));

	function lift(event: PointerEvent, id: string) {
		if (locked || event.button !== 0) return;
		const list = (event.currentTarget as HTMLElement).closest('[data-slice-strip-items]') as HTMLElement | null;
		if (!list) return;
		event.preventDefault();
		const startX = event.clientX;
		let moved = false;
		const move = (next: PointerEvent) => {
			if (!moved && Math.abs(next.clientX - startX) < 4) return;
			moved = true;
			lifted = id;
			const tiles = [...list.querySelectorAll<HTMLElement>('[data-strip-item]')];
			let index = tiles.length - 1;
			for (let i = 0; i < tiles.length; i++) {
				const box = tiles[i].getBoundingClientRect();
				if (next.clientX < box.left + box.width / 2) {
					index = i;
					break;
				}
			}
			over = index;
		};
		const up = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', up);
			window.removeEventListener('pointercancel', up);
			if (moved && over !== null) onreorder(id, over);
			lifted = null;
			over = null;
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
	}

	function keyMove(event: KeyboardEvent, id: string, index: number) {
		if (locked || !event.altKey || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
		event.preventDefault();
		onreorder(id, index + (event.key === 'ArrowRight' ? 1 : -1));
	}
</script>

<section
	class="flex flex-none flex-col border-t border-line bg-surface-1 {phone ? 'gap-1.5 px-2.5 py-2' : 'gap-2 px-4 pb-3 pt-2.5'}"
	aria-label="Output images"
	data-slice-strip
>
	{#if !phone}
		<div class="flex items-center gap-2">
			<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Output</span>
			<span class="font-mono text-sm tabular-nums text-fg">{items.length} {items.length === 1 ? 'image' : 'images'}</span>
			<span class="flex-1"></span>
			<span class="text-sm text-fg-subtle">{saved ? `Saved to ${destination}` : saving ? 'Saving in this order' : 'Drag to reorder · numbers follow'}</span>
		</div>
	{/if}
	<ol class="flex items-start gap-2.5 overflow-x-auto pt-1" style="scrollbar-width:none" data-slice-strip-items>
		{#each items as item, i (item.id)}
			{@const w = Math.round((h * item.width) / Math.max(1, item.height))}
			<li
				class="flex flex-none flex-col gap-1 transition-transform {lifted === item.id ? '-translate-y-1' : ''} {over === i && lifted !== item.id ? 'translate-x-1' : ''}"
				style="width:{Math.min(Math.max(w, phone ? 40 : 56), phone ? 96 : 160)}px"
				data-strip-item={item.n}
				data-strip-id={item.id}
			>
				<div
					class="relative overflow-hidden rounded border bg-surface-2 {lifted === item.id ? 'border-signal shadow-floating' : 'border-line-strong'} {saving || saved || locked ? '' : 'cursor-grab touch-none'}"
					style="height:{h}px"
					role="button"
					tabindex={locked ? -1 : 0}
					aria-label="Output {item.n}, {item.width}×{item.height}. Alt and arrow keys move it"
					onpointerdown={(event) => lift(event, item.id)}
					onkeydown={(event) => keyMove(event, item.id, i)}
				>
					{#if item.thumb}<img src={item.thumb} alt="" class="pointer-events-none h-full w-full object-cover {item.state === 'queued' ? 'opacity-50' : ''}" draggable="false" />{/if}
					<span class="absolute left-1 top-1 rounded-sm bg-scrim/70 px-1 font-mono text-sm leading-4 tabular-nums text-scrim-on">{item.n}</span>
					{#if item.state === 'done'}
						<span class="absolute bottom-1 right-1 grid h-5 w-5 place-items-center rounded-sm bg-success-solid text-scrim-on" aria-label="Saved"><Icon name="check" className="h-3.5 w-3.5" strokeWidth={2.4} /></span>
					{:else if item.state === 'saving'}
						<span class="absolute inset-x-1 bottom-1 h-1 overflow-hidden rounded-sm bg-scrim/60" role="progressbar" aria-label="Saving" aria-valuenow={50}><span class="block h-full w-1/2 animate-pulse bg-signal"></span></span>
					{/if}
				</div>
				{#if !phone}
					<span class="truncate font-mono text-sm tabular-nums text-fg-muted">{item.width}×{item.height}</span>
				{/if}
			</li>
		{/each}
		{#each skipped as cell (cell.id)}
			<li class="flex flex-none flex-col gap-1" style="width:{phone ? 44 : 64}px">
				<button
					type="button"
					class="relative grid place-items-center overflow-hidden rounded border border-dashed border-line-hover text-fg-subtle hover:text-fg"
					style="height:{h}px"
					aria-label="Skipped cell, keep it"
					disabled={locked}
					use:tooltip={'Keep this cell'}
					onclick={() => onrestore(cell.id)}
					data-strip-skipped
				>
					{#if cell.thumb}<img src={cell.thumb} alt="" class="absolute inset-0 h-full w-full object-cover opacity-25 grayscale" />{/if}
					<Icon name="editor-eye-off" className="relative h-4 w-4" />
				</button>
				{#if !phone}
					<span class="truncate font-mono text-sm text-fg-subtle">Skipped</span>
				{/if}
			</li>
		{/each}
	</ol>
</section>
