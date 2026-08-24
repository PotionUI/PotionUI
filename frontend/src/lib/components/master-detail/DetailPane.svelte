<script lang="ts">
	import type { Snippet } from 'svelte';
	import { createEventDispatcher } from 'svelte';

	export let title: string = '';
	export let showDelete: boolean = false;
	export let showSave: boolean = true;
	export let showCancel: boolean = true;
	export let saveLabel: string = 'Save';
	export let cancelLabel: string = 'Cancel';
	export let saveDisabled: boolean = false;
	export let isLoading: boolean = false;
	/** Extra buttons rendered between the title and the delete icon — e.g. a
	 *  per-item primary action row (Improve / Duplicate / Generate with this). */
	export let headerActions: Snippet | undefined = undefined;
	export let children: Snippet | undefined = undefined;

	const dispatch = createEventDispatcher<{
		save: void;
		cancel: void;
		delete: void;
	}>();
</script>

<div class="flex flex-col h-full">
	<!-- Header -->
	<div class="flex-shrink-0 px-6 py-4 border-b border-line flex items-center gap-3">
		<h2 class="text-lg font-semibold text-fg min-w-0 truncate">{title}</h2>
		<div class="flex-1"></div>
		{#if headerActions}
			<div class="flex items-center gap-2 flex-shrink-0">
				{@render headerActions()}
			</div>
		{/if}

		{#if showDelete}
			<button
				type="button"
				class="p-2 text-fg-muted hover:text-danger hover:bg-danger/10 rounded-lg transition-colors"
				on:click={() => dispatch('delete')}
				title="Delete"
			>
				<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
					/>
				</svg>
			</button>
		{/if}
	</div>

	<!-- Content -->
	<div class="flex-1 overflow-y-auto p-6">
		{@render children?.()}
	</div>

	<!-- Footer -->
	{#if showSave || showCancel}
		<div class="flex-shrink-0 px-6 py-4 border-t border-line flex items-center justify-end gap-3 bg-surface-1/50">
			{#if showCancel}
				<button
					type="button"
					class="px-4 py-2 text-sm bg-surface-3 text-fg-muted rounded-lg hover:bg-line-hover transition-colors"
					on:click={() => dispatch('cancel')}
					disabled={isLoading}
				>
					{cancelLabel}
				</button>
			{/if}

			{#if showSave}
				<button
					type="button"
					class="px-4 py-2 text-sm bg-accent text-accent-contrast rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
					on:click={() => dispatch('save')}
					disabled={saveDisabled || isLoading}
				>
					{#if isLoading}
						<div class="w-4 h-4 border-2 border-line-hover border-t-transparent rounded-full animate-spin"></div>
					{/if}
					{saveLabel}
				</button>
			{/if}
		</div>
	{/if}
</div>

<style>
	/* Custom scrollbar */
	.overflow-y-auto {
		scrollbar-width: thin;
		scrollbar-color: rgb(var(--line-strong)) transparent;
	}

	.overflow-y-auto::-webkit-scrollbar {
		width: 6px;
	}

	.overflow-y-auto::-webkit-scrollbar-track {
		background: transparent;
	}

	.overflow-y-auto::-webkit-scrollbar-thumb {
		background-color: rgb(var(--line-strong));
		border-radius: 3px;
	}

	.overflow-y-auto::-webkit-scrollbar-thumb:hover {
		background-color: rgb(var(--line-hover));
	}
</style>
