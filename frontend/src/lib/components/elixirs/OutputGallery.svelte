<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import OutputTile from './OutputTile.svelte';
	import { tileChoice, type Choice } from '$lib/elixirs/choice';
	import type { OutputEntry } from '$lib/elixirs/types';
	import { tileKey } from '$lib/elixirs/tiles';

	interface Props {
		entries?: OutputEntry[];
		pending?: number;
		pendingKind?: string;
		label: string;
		emptyText?: string;
		dim?: boolean;
		failed?: number;
		choice?: Choice | null;
		ontick?: (key: string) => void;
		onopen?: (entry: OutputEntry, index: number) => void;
		openerScope?: string;
	}

	let { entries = [], pending = 0, pendingKind = 'image', label, emptyText = '', dim = false, failed = 0, choice = null, ontick, onopen, openerScope = '' }: Props = $props();

	let broken = $derived(Array.from({ length: Math.max(0, failed) }, (_, index) => index));

	let skeletons = $derived(Array.from({ length: Math.max(0, pending) }, (_, index) => index));
</script>

{#if entries.length > 0 || skeletons.length > 0 || broken.length > 0 || emptyText}
	<div class="gallery" class:dim role="list" aria-label={label}>
		{#each entries as entry, index (`${tileKey(entry)}:${index}`)}
			{@const decor = tileChoice(choice, tileKey(entry))}
			<div role="listitem">
				<OutputTile {entry} opener="{openerScope}:{tileKey(entry)}:{index}" tick={decor.tick} letters={decor.letters} ontick={() => ontick?.(tileKey(entry))} onopen={() => onopen?.(entry, index)} />
			</div>
		{/each}
		{#each skeletons as index (index)}
			<div class="skeleton relative flex items-center justify-center overflow-hidden rounded border border-dashed border-line-strong bg-surface-2 text-fg-subtle" aria-hidden="true">
				<Icon name={pendingKind === 'video' ? 'film' : 'image'} className="h-5 w-5" />
			</div>
		{/each}
		{#each broken as index (index)}
			<div class="broken flex items-center justify-center rounded text-danger" role="listitem" aria-label="Not made">
				<Icon name="warning" className="h-5 w-5" />
			</div>
		{/each}
		{#if entries.length === 0 && skeletons.length === 0 && broken.length === 0}
			<p class="col-span-full rounded border border-dashed border-line-strong px-3 py-4 text-center text-sm text-fg-subtle">{emptyText}</p>
		{/if}
	</div>
{/if}

<style>
	.gallery {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 10px;
		padding: 4px 16px 16px;
	}
	.gallery.dim {
		opacity: 0.5;
	}
	.skeleton,
	.broken {
		aspect-ratio: 5 / 4;
	}
	.broken {
		border: 1px solid rgb(var(--danger) / 0.5);
		background: rgb(var(--danger) / 0.08);
	}
	.skeleton::after {
		content: '';
		position: absolute;
		inset: 0;
		background: linear-gradient(90deg, transparent, rgb(var(--fg) / 0.06), transparent);
		transform: translateX(-100%);
		animation: shimmer 1.6s infinite;
	}
	@keyframes shimmer {
		to {
			transform: translateX(100%);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.skeleton::after {
			animation: none;
		}
	}
</style>
