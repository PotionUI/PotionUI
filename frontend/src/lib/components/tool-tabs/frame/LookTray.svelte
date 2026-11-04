<script lang="ts" module>
	export interface TrayItem {
		id: string;
		label: string;
		group?: string;
		hint?: string | null;
		selected?: boolean;
	}
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	let {
		label = 'Looks',
		items,
		original = null,
		phone = false,
		thumb,
		onpick,
		onpreview
	}: {
		label?: string;
		items: TrayItem[];
		original?: { selected: boolean } | null;
		phone?: boolean;
		thumb: Snippet<[TrayItem]>;
		onpick: (id: string | null) => void;
		onpreview?: (id: string | null) => void;
	} = $props();

	const ORIGINAL_ID = '__original';
	const INTENT_MS = 140;

	let root = $state<HTMLElement>();
	let timer: ReturnType<typeof setTimeout> | null = null;
	let previewing: string | null = null;

	let originalItem = $derived<TrayItem | null>(original ? { id: ORIGINAL_ID, label: 'Original', group: 'Original', selected: original.selected } : null);
	let columns = $derived.by(() => {
		const all = originalItem ? [originalItem, ...items] : items;
		const list: { key: string; label: string | null; items: TrayItem[] }[] = [];
		for (const item of all) {
			const last = list[list.length - 1];
			if (last && last.label === (item.group ?? null)) last.items.push(item);
			else list.push({ key: `${list.length}:${item.group ?? ''}`, label: item.group ?? null, items: [item] });
		}
		return list;
	});

	function schedule(next: string | null, delay: number) {
		if (timer) clearTimeout(timer);
		timer = setTimeout(() => {
			timer = null;
			if (previewing === next) return;
			previewing = next;
			onpreview?.(next);
		}, delay);
	}

	function enter(event: PointerEvent, item: TrayItem) {
		if (event.pointerType === 'touch' || item.id === ORIGINAL_ID) return;
		schedule(item.id, previewing ? 0 : INTENT_MS);
	}

	function leave(event: PointerEvent) {
		if (event.pointerType === 'touch') return;
		schedule(null, 60);
	}

	function pick(item: TrayItem) {
		if (timer) clearTimeout(timer);
		timer = null;
		if (previewing !== null) {
			previewing = null;
			onpreview?.(null);
		}
		onpick(item.id === ORIGINAL_ID ? null : item.id);
	}

	function onkeydown(event: KeyboardEvent) {
		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Home' && event.key !== 'End') return;
		const tiles = Array.from(root?.querySelectorAll<HTMLButtonElement>('button[data-tray-item]') ?? []);
		const at = tiles.indexOf(document.activeElement as HTMLButtonElement);
		if (at < 0) return;
		event.preventDefault();
		const next = event.key === 'Home' ? 0 : event.key === 'End' ? tiles.length - 1 : Math.max(0, Math.min(tiles.length - 1, at + (event.key === 'ArrowRight' ? 1 : -1)));
		tiles[next].focus();
		tiles[next].scrollIntoView({ block: 'nearest', inline: 'nearest' });
	}

	$effect(() => () => {
		if (timer) clearTimeout(timer);
	});
</script>

<div
	bind:this={root}
	class="flex flex-none items-stretch gap-4 overflow-x-auto border-t border-line bg-surface-1 {phone ? 'px-3 pb-2 pt-2' : 'px-4 pb-3 pt-2.5'} [scrollbar-width:thin]"
	role="listbox"
	aria-label={label}
	tabindex="-1"
	{onkeydown}
	onpointerleave={leave}
	data-tray
>
	{#each columns as column (column.key)}
		<div class="flex flex-none flex-col gap-1.5" data-tray-group={column.label ?? undefined}>
			{#if column.label && !phone}
				<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{column.label}</span>
			{/if}
			<div class="flex gap-2">
				{#each column.items as item (item.id)}
					<button
						type="button"
						role="option"
						aria-selected={!!item.selected}
						class="group flex flex-none flex-col gap-1 rounded-lg p-1 text-left {phone ? 'w-[88px]' : 'w-[112px]'} {item.selected ? 'bg-signal/10' : 'hover:bg-surface-2'}"
						data-tray-item={item.id}
						data-selected={item.selected || undefined}
						onpointerenter={(event) => enter(event, item)}
						onclick={() => pick(item)}
					>
						<span class="relative block overflow-hidden rounded border bg-scrim {item.selected ? 'border-signal' : 'border-line'}">
							<span class="block aspect-[4/3] w-full">
								{@render thumb(item)}
							</span>
							{#if item.selected}
								<span class="absolute right-1 top-1 grid h-4 w-4 place-items-center rounded-sm bg-signal-solid text-scrim-on" aria-hidden="true">
									<Icon name="check" className="h-3 w-3" strokeWidth={2.5} />
								</span>
							{/if}
						</span>
						<span class="flex items-center gap-1 px-0.5 text-sm">
							<span class="min-w-0 flex-1 truncate {item.selected ? 'text-signal' : 'text-fg'}">{item.label}</span>
							{#if item.hint && !phone}<span class="font-mono text-sm text-fg-subtle">{item.hint}</span>{/if}
						</span>
					</button>
				{/each}
			</div>
		</div>
	{/each}
</div>
