<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';
	import ParametersView from '$lib/components/generation/ParametersView.svelte';
	import type { ParameterMeta, ParameterModelRef } from '$lib/components/generation/parametersModel';

	export let isOpen: boolean = false;
	export let params: Record<string, any> = {};
	export let metadata: { width: number; height: number; fileSize: string; format: string } | null =
		null;
	/** Zero-based index of the item within the batch, shown as a #N badge. */
	export let index: number = 0;
	export let meta: Record<string, ParameterMeta> | null = null;
	export let order: string[] | null = null;
	export let models: ParameterModelRef[] = [];

	const dispatch = createEventDispatcher<{ close: void }>();

</script>

<BaseModal
	{isOpen}
	title="Generation Parameters"
	sizeClass="md:w-[720px] md:max-w-[90vw]"
	on:close={() => dispatch('close')}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="sliders" className="w-5 h-5 text-fg-muted flex-shrink-0" />
	</svelte:fragment>
	<svelte:fragment slot="header">
		<Badge variant="neutral" size="sm" class="font-mono">#{index + 1}</Badge>
	</svelte:fragment>

	<div class="p-5 space-y-4">
		<!-- Media info chips -->
		{#if metadata}
			<div class="flex flex-wrap items-center gap-2">
				{#if metadata.width && metadata.height}
					<span
						class="px-2 py-1 bg-surface-2 text-fg text-xs font-mono tabular-nums rounded border border-line"
					>
						{metadata.width} × {metadata.height}
					</span>
				{/if}
				{#if metadata.fileSize && metadata.fileSize !== 'Unknown'}
					<span
						class="px-2 py-1 bg-surface-2 text-fg text-xs font-mono tabular-nums rounded border border-line"
					>
						{metadata.fileSize}
					</span>
				{/if}
				{#if metadata.format}
					<span
						class="px-2 py-1 bg-surface-2 text-fg text-xs uppercase rounded border border-line"
					>
						{metadata.format}
					</span>
				{/if}
			</div>
		{/if}

		<div class="overflow-hidden rounded-lg bg-surface-2">
			<ParametersView
				{params}
				{meta}
				{order}
				{models}
				emptyText="No generation parameters available for this item."
			/>
		</div>
	</div>
</BaseModal>
