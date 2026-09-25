<script lang="ts">
	import { onMount } from 'svelte';
	import { toasts } from '$lib/stores/toast';
	import { Button, Input, Alert, Spinner } from '$lib/components/ui';
	import { DetailSection, DETAIL_INSET_CLASS } from '$lib/components/detail';
	import { ModelsLocationState, overrideDraftsFor } from '$lib/models-location/state.svelte';

	const location = new ModelsLocationState();

	let externalPath = $state('');
	let overridesOpen = $state(false);
	let overrideDrafts = $state<Record<string, string>>({});

	onMount(async () => {
		await location.load();
		externalPath = location.config?.external_path ?? '';
		overrideDrafts = overrideDraftsFor(location.config);
	});

	async function apply() {
		const overrides = Object.fromEntries(
			Object.entries(overrideDrafts).filter(([, value]) => value.trim() !== '')
		);
		const ok = await location.apply(externalPath, overrides);
		if (ok) {
			overrideDrafts = overrideDraftsFor(location.config);
			toasts.success('Models location applied. Re-indexing in the background.');
		}
	}
</script>

<DetailSection label="Models Location">
	<div class="space-y-3">
		<p class="text-sm text-fg-muted">
			Point PotionUI at an external directory for model files. The app keeps reading
			from <span class="font-mono">models/</span> - each type directory becomes a symlink
			into the location you set here.
		</p>

		{#if location.loading}
			<Spinner size="sm" />
		{:else}
			{#if location.config?.windows_unsupported}
				<Alert variant="warning" icon
					>Relocating the models directory isn't supported on Windows yet. Move files manually
					and point individual type directories at them instead.</Alert
				>
			{/if}

			{#if location.error}
				<Alert variant="danger" icon>{location.error}</Alert>
			{/if}

			<label for="models-external-path" class="block text-sm font-medium text-fg mb-1"
				>External directory</label
			>
			<Input
				id="models-external-path"
				bind:value={externalPath}
				placeholder="/mnt/storage/models"
				disabled={location.applying}
			/>

			<button
				type="button"
				class="text-xs text-fg-muted hover:text-fg mt-2"
				onclick={() => (overridesOpen = !overridesOpen)}
			>
				{overridesOpen ? 'Hide' : 'Show'} per-type overrides
			</button>

			{#if overridesOpen && location.config}
				<div class="mt-2 space-y-2 p-3 {DETAIL_INSET_CLASS}">
					{#each location.config.directories as dir (dir.directory)}
						<div>
							<label for="override-{dir.directory}" class="block text-xs text-fg-muted mb-1"
								>{dir.directory}</label
							>
							<Input
								id="override-{dir.directory}"
								bind:value={overrideDrafts[dir.directory]}
								placeholder={dir.target ?? 'uses the external directory above'}
								disabled={location.applying}
							/>
						</div>
					{/each}
				</div>
			{/if}
		{/if}
	</div>

	{#snippet footer()}
		{#if location.config}
			<span class="mr-auto text-xs text-fg-subtle">
				{location.config.directories.filter((d) => d.linked).length} of {location.config.directories.length} type
				directories linked
			</span>
		{/if}
		<Button variant="primary" size="sm" onclick={apply} loading={location.applying} disabled={location.loading || location.applying}>
			Apply
		</Button>
	{/snippet}
</DetailSection>
