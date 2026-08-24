<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { PRESET_COLORS, type Segment } from '$lib/types/segments';

	// The "Details" reveal (name / colour / description), shared by both
	// PromptSegment.svelte variants (content and break rows). Swatch values
	// are user data, not theme tokens: they are stored on the segment and
	// round-trip through the API, so they stay literal.
	export let segment: Segment;
	export let compact = false;

	const dispatch = createEventDispatcher();

	$: currentColor = segment.color || '';

	function update(field: 'name' | 'color' | 'description', value: string) {
		dispatch('change', { [field]: value || undefined });
	}
</script>

<div class="metadata-reveal grid gap-2.5 rounded-lg bg-surface-2 {compact ? 'p-2.5' : 'p-3'} sm:grid-cols-2">
	<label class="flex min-w-0 flex-col gap-1.5">
		<span class="text-2xs text-fg-muted">Name</span>
		<input
			type="text"
			class="input w-full py-1.5 text-xs"
			value={segment.name || segment.title || ''}
			placeholder="Optional segment name"
			on:input={(event) => update('name', event.currentTarget.value)}
		/>
	</label>

	<div class="flex min-w-0 flex-col gap-1.5">
		<span class="text-2xs text-fg-muted">Colour</span>
		<div class="flex items-center gap-1.5">
			<label class="swatch-current" style={currentColor ? `background-color: ${currentColor};` : undefined}>
				<span class="sr-only">Pick a custom colour</span>
				<input
					type="color"
					class="sr-only"
					value={currentColor || PRESET_COLORS[0].value}
					on:input={(event) => update('color', event.currentTarget.value)}
				/>
			</label>
			{#each PRESET_COLORS as option (option.value)}
				<button
					type="button"
					class="swatch"
					class:selected={currentColor.toLowerCase() === option.value.toLowerCase()}
					style="background-color: {option.value};"
					aria-label={option.name}
					aria-pressed={currentColor.toLowerCase() === option.value.toLowerCase()}
					on:click={() => update('color', option.value)}
				></button>
			{/each}
			{#if currentColor}
				<button type="button" class="clear-color" on:click={() => update('color', '')}>Clear</button>
			{/if}
		</div>
	</div>

	<label class="flex min-w-0 flex-col gap-1.5 sm:col-span-2">
		<span class="text-2xs text-fg-muted">Description</span>
		<textarea
			class="input w-full resize-y py-1.5 text-xs"
			rows="2"
			value={segment.description || ''}
			placeholder="Optional notes about this segment"
			on:input={(event) => update('description', event.currentTarget.value)}
		></textarea>
	</label>
</div>

<style>
	.swatch-current {
		display: inline-flex;
		width: 1.875rem;
		height: 1.875rem;
		flex-shrink: 0;
		cursor: pointer;
		border-radius: 0.25rem;
		box-shadow: inset 0 0 0 1px rgb(var(--line-strong));
	}

	.swatch {
		width: 1.25rem;
		height: 1.25rem;
		flex-shrink: 0;
		border-radius: 0.25rem;
		box-shadow: inset 0 0 0 1px rgb(var(--line-strong) / 0.6);
		transition: transform 0.12s ease, box-shadow 0.12s ease;
	}

	.swatch:hover {
		transform: scale(1.1);
	}

	.swatch.selected {
		box-shadow: 0 0 0 2px rgb(var(--surface-2)), 0 0 0 3px rgb(var(--signal));
	}

	.clear-color {
		flex-shrink: 0;
		font-size: 0.6875rem;
		color: rgb(var(--fg-subtle));
		transition: color 0.15s ease;
	}

	.clear-color:hover {
		color: rgb(var(--fg));
	}
</style>
