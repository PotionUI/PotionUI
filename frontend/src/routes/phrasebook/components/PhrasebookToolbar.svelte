<script lang="ts">
	import { api, type PhrasebookStateFilter } from '$lib/services/api/index';
	import { PageHeader, Alert } from '$lib/components/ui';
	import { phrasebookStore } from '$lib/stores/phrasebook';

	// Self-contained: reads/writes phrasebookStore directly. Extracted
	// verbatim from phrasebook/+page.svelte (top bar + import + status banner).
	$: current = $phrasebookStore;

	let fileInputEl: HTMLInputElement;
	let rootCategoryName = '';
	let uploadStatus: string | null = null;
	let isLoading = false;

	function handleStateFilterChange(e: Event) {
		phrasebookStore.setStateFilter((e.currentTarget as HTMLSelectElement).value as PhrasebookStateFilter);
		phrasebookStore.handleStateFilterChange();
	}

	async function handleFileUpload(event: Event) {
		const target = event.target as HTMLInputElement;
		const file = target.files?.[0];
		if (!file) return;

		if (!file.name.endsWith('.yaml') && !file.name.endsWith('.yml')) {
			uploadStatus = 'Please select a YAML file (.yaml or .yml)';
			return;
		}

		isLoading = true;
		uploadStatus = null;

		try {
			const response = await api.importPhrasebookYAML(file, rootCategoryName || undefined);
			if (response.success && response.data) {
				uploadStatus = `Imported ${response.data.categories_created} categories and ${response.data.values_created} values`;
				await phrasebookStore.loadRootCategories();
				await phrasebookStore.loadAllCategories();
				rootCategoryName = '';
			} else {
				uploadStatus = response.error || 'Import failed';
			}
		} catch (error) {
			uploadStatus = 'Failed to upload file';
		} finally {
			isLoading = false;
			if (fileInputEl) fileInputEl.value = '';
		}
	}
</script>

<PageHeader sticky={false}>
	<div class="flex items-center gap-3">
		<svg class="w-5 h-5 text-fg-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14" />
		</svg>
		<span class="text-sm font-semibold text-fg">Phrasebook Management</span>
	</div>

	<div class="flex items-center gap-3">
		<!-- State filter -->
		<select
			class="px-3 py-1.5 text-xs border border-line-strong rounded-lg bg-surface-2 text-fg focus:outline-none focus:ring-2 focus:ring-accent"
			value={current.stateFilter}
			on:change={handleStateFilterChange}
		>
			<option value="all">All</option>
			<option value="active">Active</option>
			<option value="inactive">Inactive</option>
		</select>

		<!-- Import section -->
		<input
			type="text"
			class="input py-1.5 text-xs w-40"
			placeholder="Root category name"
			bind:value={rootCategoryName}
		/>
		<button
			class="btn-header-secondary"
			on:click={() => fileInputEl?.click()}
			disabled={isLoading}
		>
			<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
			</svg>
			Import
		</button>
		<input bind:this={fileInputEl} type="file" accept=".yaml,.yml" on:change={handleFileUpload} class="hidden" />

		<button class="btn-header-primary" on:click={() => phrasebookStore.handleNewCategory()}>
			<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
			</svg>
			New Category
		</button>
	</div>
</PageHeader>

<!-- Status message -->
{#if uploadStatus}
	<div class="px-6">
		<Alert variant={uploadStatus.includes('Imported') ? 'success' : 'danger'} density="compact">
			{uploadStatus}
			{#snippet actions()}
				<button class="underline" on:click={() => (uploadStatus = null)}>dismiss</button>
			{/snippet}
		</Alert>
	</div>
{/if}
