<script lang="ts">
	import FieldChildren from './FieldChildren.svelte';

	export let name: string | null;
	export let config: any;
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	export let onOriginChange: ((fieldName: string, origin: unknown) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	export let fieldPath: string | undefined = undefined;

	$: label = config.title || name || '';
</script>

<div class="border-l-2 border-line pl-3 space-y-1 pb-3 mb-4">
	<fieldset class="border-0 p-0 m-0 min-w-0">
		{#if config.label || label}
			<legend class="font-mono text-xs font-semibold text-fg-muted uppercase tracking-[0.08em] mb-2">
				{config.label || label}
			</legend>
		{:else}
			<legend class="sr-only">{name || ''}</legend>
		{/if}
		<div class="space-y-2">
			<FieldChildren children={config.children || []} {value} {onChange} {onOriginChange} {onMaskChange} location="group" {fieldPath} />
		</div>
	</fieldset>
</div>
