<script lang="ts">
	import { getContext } from 'svelte';
	import FieldChildren from './FieldChildren.svelte';
	import { resolveRowLayout } from './rowLayout';
	import { viewportWidth } from '$lib/stores/viewport';
	import { settingsPaneContentWidth } from '$lib/stores/generationLayout';
	import { ROW_INSET_CONTEXT_KEY } from './rowInset';

	export let name: string | null;
	export let config: any;
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	export let onOriginChange: ((fieldName: string, origin: unknown) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	export let fieldPath: string | undefined = undefined;

	// The settings pane's content width is a fixed function of the viewport
	// tier (no ResizeObserver needed), but a row nested inside a section's
	// children well renders narrower than that: subtract the accumulated
	// inset any ancestor wells contributed (rowInset.ts) to find the width
	// actually available to this row's tracks.
	const inset = getContext<number | undefined>(ROW_INSET_CONTEXT_KEY) ?? 0;
	$: layout = resolveRowLayout(config, settingsPaneContentWidth($viewportWidth) - inset);
</script>

<div
	class="row-grid grid gap-2.5"
	data-field-name={name || undefined}
	style:grid-template-columns={layout.gridTemplateColumns}
>
	<FieldChildren
		children={config.children || []}
		{value}
		{onChange}
		{onOriginChange}
		{onMaskChange}
		location="row"
		allowTitleFallback={true}
		{fieldPath}
	/>
</div>

<style>
	/* Grid items default to `min-width: auto` (their content's min-content
	   size), which can force a track wider than its `1fr` share and overflow
	   the row instead of shrinking. Row children render with no wrapper div
	   (see FieldChildren.svelte), so this reaches each child field's own root
	   element directly. */
	.row-grid > :global(*) {
		min-width: 0;
	}
</style>
