<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { TrayItem } from './LookTray.svelte';

	let {
		label = 'Looks',
		items,
		original = null,
		thumb,
		onpick,
		onpreview
	}: {
		label?: string;
		items: TrayItem[];
		original?: { selected: boolean } | null;
		thumb: Snippet<[TrayItem]>;
		onpick: (id: string | null) => void;
		onpreview?: (id: string | null) => void;
	} = $props();

	const ORIGINAL_ID = '__original';
	const INTENT_MS = 140;

	let root = $state<HTMLElement>();
	let timer: ReturnType<typeof setTimeout> | null = null;
	let previewing: string | null = null;

	let sections = $derived.by(() => {
		const list: { key: string; label: string | null; items: TrayItem[] }[] = [];
		for (const item of items) {
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

	function enter(event: PointerEvent, id: string) {
		if (event.pointerType === 'touch' || id === ORIGINAL_ID) return;
		schedule(id, previewing ? 0 : INTENT_MS);
	}

	function leave(event: PointerEvent) {
		if (event.pointerType === 'touch') return;
		schedule(null, 60);
	}

	function pick(id: string) {
		if (timer) clearTimeout(timer);
		timer = null;
		if (previewing !== null) {
			previewing = null;
			onpreview?.(null);
		}
		onpick(id === ORIGINAL_ID ? null : id);
	}

	function onkeydown(event: KeyboardEvent) {
		const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'];
		if (!keys.includes(event.key)) return;
		const tiles = Array.from(root?.querySelectorAll<HTMLButtonElement>('button[data-look-item]') ?? []);
		const at = tiles.indexOf(document.activeElement as HTMLButtonElement);
		if (at < 0) return;
		event.preventDefault();
		const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1;
		const next = event.key === 'Home' ? 0 : event.key === 'End' ? tiles.length - 1 : Math.max(0, Math.min(tiles.length - 1, at + step));
		tiles[next].focus();
	}

	$effect(() => () => {
		if (timer) clearTimeout(timer);
	});
</script>

<div bind:this={root} class="flex flex-col gap-3" role="listbox" aria-label={label} tabindex="-1" {onkeydown} onpointerleave={leave} data-look-list>
	{#if original}
		<button
			type="button"
			role="option"
			aria-selected={original.selected}
			class="flex h-11 w-full items-center gap-2.5 rounded-lg border px-1.5 text-left {original.selected ? 'border-signal/60 bg-signal/10' : 'border-line bg-surface-1 hover:border-line-hover'}"
			data-look-item={ORIGINAL_ID}
			data-selected={original.selected || undefined}
			onclick={() => pick(ORIGINAL_ID)}
		>
			<span class="block h-8 w-11 flex-none overflow-hidden rounded bg-scrim">{@render thumb({ id: ORIGINAL_ID, label: 'Original' })}</span>
			<span class="min-w-0 flex-1 truncate text-base {original.selected ? 'text-signal' : 'text-fg'}">Original</span>
			<span class="text-sm text-fg-subtle">No look</span>
		</button>
	{/if}
	{#each sections as section (section.key)}
		<div class="flex flex-col gap-1.5" data-look-group={section.label ?? undefined}>
			{#if section.label}
				<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{section.label}</span>
			{/if}
			<div class="grid grid-cols-5 gap-1.5">
				{#each section.items as item (item.id)}
					<button
						type="button"
						role="option"
						aria-selected={!!item.selected}
						class="flex min-w-0 flex-col gap-1 rounded text-left"
						data-look-item={item.id}
						data-selected={item.selected || undefined}
						onpointerenter={(event) => enter(event, item.id)}
						onclick={() => pick(item.id)}
					>
						<span class="relative block overflow-hidden rounded border bg-scrim {item.selected ? 'border-signal' : 'border-line'}">
							<span class="block aspect-[4/3] w-full">{@render thumb(item)}</span>
							{#if item.selected}
								<span class="absolute right-0.5 top-0.5 grid h-4 w-4 place-items-center rounded-sm bg-signal-solid text-scrim-on" aria-hidden="true">
									<Icon name="check" className="h-3 w-3" strokeWidth={2.5} />
								</span>
							{/if}
						</span>
						<span class="truncate text-sm {item.selected ? 'text-signal' : 'text-fg-muted'}">{item.label}</span>
					</button>
				{/each}
			</div>
		</div>
	{/each}
</div>
