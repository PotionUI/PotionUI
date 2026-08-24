<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';
	import { copyText } from '$lib/utils/clipboard';

	export let isOpen: boolean = false;
	export let params: Record<string, any> = {};
	export let metadata: { width: number; height: number; fileSize: string; format: string } | null =
		null;
	/** Zero-based index of the item within the batch, shown as a #N badge. */
	export let index: number = 0;

	const dispatch = createEventDispatcher<{ close: void }>();

	$: entries = Object.entries(params ?? {});

	function copyToClipboard(value: unknown) {
		void copyText(typeof value === 'object' ? JSON.stringify(value) : String(value));
	}
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

		<!-- Parameter cards -->
		{#if entries.length > 0}
			<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
				{#each entries as [key, value]}
					<div
						class="group relative bg-surface-2 rounded-lg p-3 border border-line hover:border-line-hover transition-colors"
					>
						<div class="flex items-center justify-between mb-1.5">
							<span class="text-xs font-medium text-fg-muted capitalize truncate">
								{key.replace(/_/g, ' ')}
							</span>
							<button
								on:click={() => copyToClipboard(value)}
								class="opacity-0 group-hover:opacity-100 p-1 hover:bg-surface-3 rounded transition-all"
								title="Copy to clipboard"
								aria-label={`Copy ${key}`}
							>
								<Icon name="copy" className="w-3.5 h-3.5 text-fg-subtle" />
							</button>
						</div>
						<div
							class="font-mono text-sm font-semibold text-fg tabular-nums max-h-28 overflow-auto break-words"
						>
							{typeof value === 'object' ? JSON.stringify(value) : value}
						</div>
					</div>
				{/each}
			</div>
		{:else}
			<div class="text-sm text-fg-subtle py-6 text-center">
				No generation parameters available for this item.
			</div>
		{/if}
	</div>
</BaseModal>
