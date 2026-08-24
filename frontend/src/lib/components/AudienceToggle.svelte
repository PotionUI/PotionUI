<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { formAudienceStore } from '$lib/stores/formAudience';

	// Self-contained: reads/writes formAudienceStore directly, no props needed.
	// Extracted from PresetSessionBar for the tabs-row relocation.
</script>

<Tooltip
	text="Simple hides advanced-only fields; their values still submit with saved/default settings."
	position="bottom"
	delay={150}
	wrapperClass="flex w-full"
>
	<div class="flex w-full items-center bg-surface-2 rounded-lg p-0.5">
		<button
			type="button"
			class="flex-1 px-2.5 py-1 text-xs font-medium rounded-md transition-all {$formAudienceStore === 'simple'
				? 'bg-signal/10 text-signal shadow-sm'
				: 'text-fg-muted hover:text-fg hover:bg-surface-3/50'}"
			on:click={() => formAudienceStore.set('simple')}
		>
			Simple
		</button>
		<button
			type="button"
			class="flex-1 px-2.5 py-1 text-xs font-medium rounded-md transition-all {$formAudienceStore === 'advanced'
				? 'bg-signal/10 text-signal shadow-sm'
				: 'text-fg-muted hover:text-fg hover:bg-surface-3/50'}"
			on:click={() => formAudienceStore.set('advanced')}
		>
			Advanced
		</button>
	</div>
</Tooltip>
