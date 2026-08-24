<script lang="ts">
	import { onMount } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api';
	import { toasts } from '$lib/stores/toast';
	import { Button, Input, Alert, Spinner } from '$lib/components/ui';
	import type { ModelsLocationConfig } from '$lib/services/api/models';

	let config = $state<ModelsLocationConfig | null>(null);
	let loading = $state(true);
	let applying = $state(false);
	let error = $state<string | null>(null);
	let externalPath = $state('');
	let overridesOpen = $state(false);
	let overrideDrafts = $state<Record<string, string>>({});

	onMount(load);

	async function load() {
		loading = true;
		error = null;
		try {
			const response = await api.getModelsLocation();
			if (response.success && response.data) {
				config = response.data;
				externalPath = response.data.external_path ?? '';
				overrideDrafts = { ...response.data.overrides };
			} else {
				error = response.message ?? 'Failed to load the models location.';
			}
		} catch (e) {
			logger.error('Failed to load models location:', e);
			error = 'Failed to load the models location.';
		} finally {
			loading = false;
		}
	}

	async function apply() {
		applying = true;
		error = null;
		try {
			const overrides = Object.fromEntries(
				Object.entries(overrideDrafts).filter(([, value]) => value.trim() !== '')
			);
			const response = await api.applyModelsLocation(externalPath, overrides);
			if (response.success && response.data) {
				config = response.data;
				overrideDrafts = { ...response.data.overrides };
				toasts.success('Models location applied. Re-indexing in the background.');
			} else {
				error = response.message ?? 'Failed to apply the models location.';
			}
		} catch (e) {
			logger.error('Failed to apply models location:', e);
			error = 'Failed to apply the models location.';
		} finally {
			applying = false;
		}
	}
</script>

<div class="bg-surface-1 rounded-lg border border-line shadow-raised">
	<div class="px-6 py-3 border-b border-line">
		<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Models Location</h3>
	</div>

	<div class="px-6 py-4 space-y-3">
	<p class="text-sm text-fg-muted">
		Point PotionUI at an external directory for model files. The app keeps reading
		from <span class="font-mono">models/</span> - each type directory becomes a symlink
		into the location you set here.
	</p>

	{#if loading}
		<Spinner size="sm" />
	{:else}
		{#if config?.windows_unsupported}
			<Alert variant="warning" icon
				>Relocating the models directory isn't supported on Windows yet. Move files manually
				and point individual type directories at them instead.</Alert
			>
		{/if}

		{#if error}
			<Alert variant="danger" icon>{error}</Alert>
		{/if}

		<label for="models-external-path" class="block text-sm font-medium text-fg mb-1"
			>External directory</label
		>
		<Input
			id="models-external-path"
			bind:value={externalPath}
			placeholder="/mnt/storage/models"
			disabled={applying}
		/>

		<button
			type="button"
			class="text-xs text-fg-muted hover:text-fg mt-2"
			onclick={() => (overridesOpen = !overridesOpen)}
		>
			{overridesOpen ? 'Hide' : 'Show'} per-type overrides
		</button>

		{#if overridesOpen && config}
			<div class="mt-2 space-y-2 border border-line rounded p-3">
				{#each config.directories as dir (dir.directory)}
					<div>
						<label for="override-{dir.directory}" class="block text-xs text-fg-muted mb-1"
							>{dir.directory}</label
						>
						<Input
							id="override-{dir.directory}"
							bind:value={overrideDrafts[dir.directory]}
							placeholder={dir.target ?? 'uses the external directory above'}
							disabled={applying}
						/>
					</div>
				{/each}
			</div>
		{/if}

		<div class="flex items-center gap-3 mt-3">
			<Button variant="primary" onclick={apply} loading={applying} disabled={applying}
				>Apply</Button
			>
			{#if config}
				<span class="text-xs text-fg-subtle">
					{config.directories.filter((d) => d.linked).length} of {config.directories.length} type
					directories linked
				</span>
			{/if}
		</div>
	{/if}
	</div>
</div>
