<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { PRESET_COLORS, type Segment } from '$lib/types/segments';

	// The name / colour / description / prefix / suffix fields, hosted by
	// PromptSegmentDetailsModal.svelte. Swatch values are user data, not
	// theme tokens: they are stored on the segment and round-trip through
	// the API, so they stay literal.
	export let segment: Segment;

	const dispatch = createEventDispatcher();

	$: currentColor = segment.color || '';

	function update(field: 'name' | 'color' | 'description' | 'prefix' | 'suffix', value: string) {
		dispatch('change', { [field]: value || undefined });
	}
</script>

<!--
	Anatomy/classes match the mock's Segment details modal body (`.form-grid` /
	`.field-label` / `.field` / `.swatches` / `.swatch`) — visuals come from
	segment-composer.css via the `.segment-composer` wrapper PromptSegmentDetailsModal
	provides, since BaseModal portals this content onto <body>.
-->
<div class="form-grid">
	<label class="flex min-w-0 flex-col">
		<span class="field-label">Name</span>
		<input
			type="text"
			class="field"
			value={segment.name || segment.title || ''}
			placeholder="Optional segment name"
			on:input={(event) => update('name', event.currentTarget.value)}
		/>
	</label>

	<div class="flex min-w-0 flex-col">
		<span class="field-label">Colour</span>
		<div class="swatches">
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
					class:active={currentColor.toLowerCase() === option.value.toLowerCase()}
					style="--swatch: {option.value};"
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

	<label class="flex min-w-0 flex-col">
		<span class="field-label">Prefix</span>
		<input
			type="text"
			class="field font-mono"
			value={segment.prefix || ''}
			placeholder="Joined before the text"
			on:input={(event) => update('prefix', event.currentTarget.value)}
		/>
	</label>

	<label class="flex min-w-0 flex-col">
		<span class="field-label">Suffix</span>
		<input
			type="text"
			class="field font-mono"
			value={segment.suffix || ''}
			placeholder="Joined after the text"
			on:input={(event) => update('suffix', event.currentTarget.value)}
		/>
	</label>

	<label class="full flex min-w-0 flex-col">
		<span class="field-label">Description</span>
		<textarea
			class="field"
			rows="2"
			value={segment.description || ''}
			placeholder="Optional notes about this segment"
			on:input={(event) => update('description', event.currentTarget.value)}
		></textarea>
	</label>
</div>
