<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button } from '$lib/components/ui';
	import OutputTile from './OutputTile.svelte';
	import { layoutMosaic, MOSAIC_GAP, previewMosaic, rememberAspect } from '$lib/elixirs/mosaic';
	import type { OutputEntry } from '$lib/elixirs/types';
	import { tileName } from '$lib/elixirs/tiles';

	interface Props {
		entries: OutputEntry[];
		label: string;
		maxRows?: number;
		drawn?: Set<string> | null;
		openerScope?: string;
		onopen?: (entry: OutputEntry, index: number, shownEntries: OutputEntry[]) => void;
		onremove?: (index: number) => void;
		moreLabel?: (shown: number, total: number) => string;
		onmore?: () => void;
	}

	let { entries, label, maxRows = Infinity, drawn = null, openerScope = '', onopen, onremove, moreLabel, onmore }: Props = $props();

	let width = $state(0);
	let version = $state(0);
	let scheduled = false;

	function measured(entry: OutputEntry, naturalWidth: number, naturalHeight: number) {
		if (Number(entry.width) > 0 && Number(entry.height) > 0) return;
		if (!rememberAspect(entry, naturalWidth, naturalHeight) || scheduled) return;
		scheduled = true;
		requestAnimationFrame(() => {
			scheduled = false;
			version += 1;
		});
	}

	let mosaic = $derived.by(() => {
		void version;
		return drawn ? previewMosaic(entries, width, drawn, maxRows) : layoutMosaic(entries, width, maxRows);
	});
	let complete = $derived(mosaic.shown >= mosaic.total);
	let lastKey = $derived(mosaic.tiles.length ? `${mosaic.tiles[mosaic.tiles.length - 1].key}:${mosaic.tiles[mosaic.tiles.length - 1].index}` : '');
</script>

<div class="mosaic-wrap">
	<div class="mosaic" role="list" aria-label={label} bind:clientWidth={width} style:gap="{MOSAIC_GAP}px" data-shown={mosaic.shown} data-total={mosaic.total}>
		{#each mosaic.tiles as tile (`${tile.key}:${tile.index}`)}
			{@const undrawn = !!drawn && !drawn.has(tile.key)}
			{@const last = `${tile.key}:${tile.index}` === lastKey}
			<div
				role="listitem"
				class="cell"
				class:undrawn
				data-drawn={drawn ? String(!undrawn) : undefined}
				style:width="{tile.width}px"
				style:height="{tile.height}px"
				style:flex-grow={tile.rowEnd && !(complete && last) ? 1 : 0}
			>
				<OutputTile
					fill
					entry={tile.entry}
					opener="{openerScope}:{tile.key}:{tile.index}"
					onopen={() => onopen?.(tile.entry, tile.index, mosaic.entries)}
					onmeasure={(naturalWidth, naturalHeight) => measured(tile.entry, naturalWidth, naturalHeight)}
				/>
				{#if undrawn}
					<span class="sr-only">Not drawn this brew</span>
				{/if}
				{#if onremove}
					<span class="remove">
						<Tooltip text="Remove from this source">
							<button type="button" class="remove-button" aria-label="Remove {tileName(tile.entry)}" onclick={() => onremove?.(tile.index)}>
								<Icon name="close" className="h-3.5 w-3.5" />
							</button>
						</Tooltip>
					</span>
				{/if}
			</div>
		{/each}
	</div>
	{#if !complete && onmore && moreLabel}
		<div class="more" data-no-open>
			<Button variant="secondary" size="sm" onclick={() => onmore?.()}>
				<span class="font-mono tabular-nums">{moreLabel(mosaic.shown, mosaic.total)}</span>
			</Button>
		</div>
	{/if}
</div>

<style>
	.mosaic {
		display: flex;
		flex-wrap: wrap;
		align-content: flex-start;
		width: 100%;
	}
	.cell {
		position: relative;
		flex-shrink: 0;
		min-width: 0;
	}
	.undrawn {
		opacity: 0.35;
		transition: opacity 150ms ease;
	}
	.undrawn:hover,
	.undrawn:focus-within {
		opacity: 0.8;
	}
	.more {
		display: flex;
		justify-content: center;
		padding-top: 8px;
	}
	.remove {
		position: absolute;
		top: 4px;
		right: 4px;
		z-index: 10;
		opacity: 0;
		transition: opacity 120ms ease;
	}
	.cell:hover .remove,
	.cell:focus-within .remove {
		opacity: 1;
	}
	@media (hover: none) {
		.remove {
			opacity: 1;
		}
	}
	.remove-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 24px;
		height: 24px;
		border-radius: 4px;
		background: rgb(var(--canvas) / 0.85);
		color: rgb(var(--fg));
		border: 1px solid rgb(var(--line-strong));
	}
	.remove-button:hover {
		background: rgb(var(--danger-solid));
	}
	@media (prefers-reduced-motion: reduce) {
		.undrawn,
		.remove {
			transition: none;
		}
	}
</style>
