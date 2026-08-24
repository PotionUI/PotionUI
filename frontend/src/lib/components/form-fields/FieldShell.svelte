<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';

	export let name: string | null = null;
	export let label = '';
	export let description = '';
	export let tooltip: string | undefined = undefined;
	export let labelId: string | undefined = undefined;
	export let labelFor: string | undefined = undefined;
	export let descriptionSpacing: 'top' | 'bottom' = 'top';

	$: resolvedDescriptionId = description && name ? `${name}-desc` : undefined;
</script>

<div class="field-card">
	<div class="flex items-center justify-between gap-2 {descriptionSpacing === 'bottom' ? 'mb-1' : ''}">
		<div class="flex min-w-0 items-center gap-1">
			<svelte:element
				this={labelFor ? 'label' : 'span'}
				id={labelId}
				for={labelFor}
				class="label {descriptionSpacing === 'bottom' ? '!mb-0' : ''}"
			>
				{label}
			</svelte:element>
			{#if tooltip}
				<Tooltip text={tooltip} position="top">
					<span class="inline-flex cursor-help items-center text-fg-subtle">
						<Icon name="info" className="h-3.5 w-3.5" />
					</span>
				</Tooltip>
			{/if}
		</div>
		{#if $$slots.actions}<slot name="actions" />{/if}
	</div>
	{#if description && descriptionSpacing === 'bottom'}
		<p id={resolvedDescriptionId} class="mb-1 text-xs text-fg-muted">{description}</p>
	{/if}
	<slot descriptionId={resolvedDescriptionId} />
	{#if description && descriptionSpacing === 'top'}
		<p id={resolvedDescriptionId} class="mt-1 text-xs text-fg-muted">{description}</p>
	{/if}
</div>
