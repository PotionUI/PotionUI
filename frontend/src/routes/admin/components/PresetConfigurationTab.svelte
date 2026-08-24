<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import TagSelector from '$lib/components/TagSelector.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Spinner, EmptyState } from '$lib/components/ui';
	import type { PresetConfigurationEntry } from '$lib/types/api';

	export let presetId: string;
	export let initialEntries: PresetConfigurationEntry[] = [];

	let entries: PresetConfigurationEntry[] = initialEntries;
	let pendingValues: Record<string, unknown> = valuesFrom(initialEntries);
	let loading = initialEntries.length === 0;
	let loadError = '';
	let savingKey: string | null = null;
	let savedKey: string | null = null;
	let savedTimer: ReturnType<typeof setTimeout> | null = null;

	function valuesFrom(list: PresetConfigurationEntry[]): Record<string, unknown> {
		return Object.fromEntries(list.map((entry) => [entry.key, entry.value]));
	}

	onMount(() => {
		if (initialEntries.length === 0) load();
	});

	async function load() {
		loading = true;
		loadError = '';
		try {
			const response = await api.getPresetConfiguration(presetId);
			if (!response.success || !response.data) {
				throw new Error(response.message || 'Could not load configuration');
			}
			entries = response.data.entries || [];
			pendingValues = valuesFrom(entries);
		} catch (error) {
			logger.error('Failed to load preset configuration:', error);
			loadError = error instanceof Error ? error.message : 'Could not load configuration';
		} finally {
			loading = false;
		}
	}

	function tagIdsFor(key: string): string[] {
		const value = pendingValues[key];
		return Array.isArray(value) ? value.map(String) : [];
	}

	function handleTagsChange(key: string, event: CustomEvent<string[]>) {
		pendingValues = { ...pendingValues, [key]: event.detail };
	}

	async function save(entry: PresetConfigurationEntry) {
		savingKey = entry.key;
		try {
			const response = await api.updatePresetConfiguration(presetId, {
				[entry.key]: pendingValues[entry.key]
			});
			if (!response.success || !response.data) {
				throw new Error(response.message || 'Could not save configuration');
			}
			entries = response.data.entries || entries;
			pendingValues = valuesFrom(entries);
			toasts.success(`${entry.label} saved`);
			savedKey = entry.key;
			if (savedTimer) clearTimeout(savedTimer);
			savedTimer = setTimeout(() => (savedKey = null), 2000);
		} catch (error) {
			logger.error('Failed to save preset configuration:', error);
			toasts.error(error instanceof Error ? error.message : 'Could not save configuration');
		} finally {
			savingKey = null;
		}
	}
</script>

<div class="space-y-5">
	{#if loading}
		<div class="rounded-lg border border-line bg-surface-1 py-10 flex flex-col items-center justify-center">
			<Spinner size="md" />
			<p class="text-sm text-fg-muted mt-3">Loading configuration…</p>
		</div>
	{:else if loadError}
		<EmptyState title="Configuration unavailable" description={loadError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={load}>Try again</Button>{/snippet}
		</EmptyState>
	{:else if entries.length === 0}
		<EmptyState title="No configuration" description="This preset does not declare any configuration entries." icon="sliders" compact />
	{:else}
		{#each entries as entry (entry.key)}
			<div class="rounded-lg border border-line bg-surface-1 p-4 sm:p-5">
				<div class="flex items-start justify-between gap-3 mb-3">
					<div class="min-w-0">
						<p class="text-sm font-medium text-fg">{entry.label}</p>
						{#if entry.description}
							<p class="text-xs text-fg-muted mt-0.5">{entry.description}</p>
						{/if}
					</div>
					<Button
						variant="secondary"
						size="sm"
						icon={savedKey === entry.key ? 'check' : 'save'}
						loading={savingKey === entry.key}
						disabled={savingKey !== null}
						onclick={() => save(entry)}
					>{savedKey === entry.key ? 'Saved' : 'Save'}</Button>
				</div>

				{#if entry.type === 'model_tags'}
					<TagSelector
						tagType="MODEL"
						selectedTagIds={tagIdsFor(entry.key)}
						placeholder="Select model tags…"
						on:change={(event) => handleTagsChange(entry.key, event)}
					/>
				{:else}
					<div class="flex items-center gap-2 text-xs text-fg-subtle rounded border border-dashed border-line-strong px-3 py-2">
						<Icon name="info" className="w-3.5 h-3.5 flex-shrink-0" />
						Unsupported configuration type "{entry.type}".
					</div>
				{/if}
			</div>
		{/each}
	{/if}
</div>
