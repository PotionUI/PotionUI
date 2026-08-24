<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';

	// The "which model is this import for" control shared by the CivitAI and
	// text/file prompt-import modals: a chip once a model is chosen, or a
	// search + dropdown picker before one is.
	export let models: Array<{ id: string; filename?: string; name?: string }>;
	export let modelId: string;
	export let onModelIdChange: (id: string) => void;
	export let label = 'Local Model (optional)';
	export let idPrefix = 'model-context-picker';

	function getModelName(id: string): string {
		const model = models.find((item) => item.id === id);
		return model?.filename ?? model?.name ?? id;
	}

	let searchOpen = false;
	let searchValue = '';

	$: filteredModels = searchValue.trim()
		? models.filter((m) => (m.filename ?? m.name ?? '').toLowerCase().includes(searchValue.toLowerCase()))
		: models;
</script>

{#if modelId}
	<div class="bg-surface-2 border border-line-strong rounded-lg px-3 py-2.5 flex items-center gap-2">
		<Icon name="model" className="w-3.5 h-3.5 text-fg-muted flex-shrink-0" />
		<div class="min-w-0 flex-1">
			<p class="text-2xs text-fg-subtle mb-0.5">Importing for model</p>
			<p class="text-xs text-fg font-medium truncate">{getModelName(modelId)}</p>
		</div>
		<button
			class="text-2xs text-fg-subtle hover:text-fg-muted transition-colors flex-shrink-0"
			on:click={() => onModelIdChange('')}
			title="Remove model association"
		>
			<Icon name="close" className="w-3.5 h-3.5" />
		</button>
	</div>
{:else}
	<div class="relative">
		<label class="block text-xs font-medium text-fg-muted mb-1.5" for="{idPrefix}-search">{label}</label>
		<button
			id="{idPrefix}-search"
			class="input text-xs py-2 w-full text-left flex items-center justify-between cursor-pointer"
			on:click={() => { searchOpen = !searchOpen; searchValue = ''; }}
		>
			<span class="text-fg-subtle">None — prompts won't be linked to a model</span>
			<Icon name="chevron-down" className="w-3 h-3 text-fg-muted flex-shrink-0" />
		</button>
		{#if searchOpen}
			<div class="absolute top-full left-0 mt-1 w-full bg-surface-1 border border-line-strong rounded-lg shadow-floating z-50 overflow-hidden">
				<div class="p-2 border-b border-line">
					<input
						type="text"
						class="input text-xs py-1.5 w-full"
						placeholder="Search models..."
						bind:value={searchValue}
					/>
				</div>
				<div class="max-h-48 overflow-y-auto">
					{#each filteredModels as model (model.id)}
						<button
							class="w-full text-left px-3 py-2 text-xs text-fg-muted hover:bg-surface-3 transition-colors truncate"
							on:click={() => { onModelIdChange(model.id); searchOpen = false; searchValue = ''; }}
						>
							{model.filename ?? model.name ?? 'Unknown'}
						</button>
					{/each}
					{#if searchValue.trim() && filteredModels.length === 0}
						<p class="px-3 py-3 text-xs text-fg-subtle text-center">No models match</p>
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/if}
