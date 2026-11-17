<script lang="ts" generics="T extends { key: string; aspect?: number | null }">
	import type { Snippet } from 'svelte';
	import { clampAspect, flattenJustifiedRows, layoutJustifiedRows } from '$lib/utils/justifiedLayout';

	type TileBox = { width: number; height: number };

	let {
		items,
		maxRows = 0,
		gap = 12,
		rowHeight = 0,
		fallbackAspect = 4 / 3,
		tile,
		...rest
	}: {
		items: T[];
		maxRows?: number;
		gap?: number;
		rowHeight?: number;
		fallbackAspect?: number;
		tile: Snippet<[T, TileBox, (width: number, height: number) => void]>;
		[attr: string]: unknown;
	} = $props();

	let gridWidth = $state(0);
	let natural = $state<Record<string, number>>({});

	let targetRowHeight = $derived(rowHeight > 0 ? rowHeight : Math.round(gridWidth > 0 ? Math.max(180, Math.min(320, gridWidth / 4.6)) : 260));

	function aspectOf(item: T): number {
		if (item.aspect && item.aspect > 0) return clampAspect(item.aspect);
		return clampAspect(natural[item.key] ?? fallbackAspect);
	}

	let boxes = $derived.by(() => {
		let rows = layoutJustifiedRows(
			items.map((item) => ({ item, aspect: aspectOf(item) })),
			gridWidth,
			targetRowHeight,
			gap
		);
		const lastRow = rows.length - 1;
		if (maxRows > 0) rows = rows.slice(0, maxRows);
		return rows.flatMap((row, index) =>
			flattenJustifiedRows([row]).map((box) => ({ ...box, grow: box.rowEnd && index < lastRow }))
		);
	});

	function report(key: string, width: number, height: number) {
		if (!(width > 0 && height > 0)) return;
		const ratio = width / height;
		if (natural[key] !== undefined && Math.abs(natural[key] - ratio) < 0.001) return;
		natural[key] = ratio;
	}
</script>

<div bind:clientWidth={gridWidth} class="min-w-0" {...rest}>
	{#if gridWidth > 0}
		<div class="flex flex-wrap items-start" style="gap: {gap}px">
			{#each boxes as box (box.item.key)}
				<div
					class="min-w-0"
					data-row-end={box.rowEnd}
					data-tile-box
					style="flex: {box.grow ? '1 1' : '0 0'} {box.width}px; max-width: 100%"
				>
					{@render tile(box.item, { width: box.width, height: box.height }, (w, h) => report(box.item.key, w, h))}
				</div>
			{/each}
		</div>
	{/if}
</div>
