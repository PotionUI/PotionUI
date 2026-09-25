<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { invalidatePresets } from '$lib/stores/presetsCatalog';
	import TagSelector from '$lib/components/TagSelector.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner, EmptyState, Button } from '$lib/components/ui';
	import { DetailSection, DETAIL_INSET_CLASS } from '$lib/components/detail';
	import { valuesFrom, dirtyKeysOf, buildSavePayload } from './presetConfigurationDraft';
	import type { PresetConfigurationEntry } from '$lib/types/api';

	let {
		presetId,
		initialEntries = [],
		dirtyCount = $bindable(0),
		saving = $bindable(false)
	}: {
		presetId: string;
		initialEntries?: PresetConfigurationEntry[];
		dirtyCount?: number;
		saving?: boolean;
	} = $props();

	let entries = $state<PresetConfigurationEntry[]>(initialEntries);
	let originalValues = $state<Record<string, unknown>>(valuesFrom(initialEntries));
	let pendingValues = $state<Record<string, unknown>>(valuesFrom(initialEntries));
	let loading = $state(initialEntries.length === 0);
	let loadError = $state('');

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
			originalValues = valuesFrom(entries);
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

	const dirtyKeys = $derived(dirtyKeysOf(entries, pendingValues, originalValues));
	$effect(() => {
		dirtyCount = dirtyKeys.length;
	});

	export async function save() {
		if (dirtyKeys.length === 0) return;
		saving = true;
		try {
			const values = buildSavePayload(dirtyKeys, pendingValues);
			const response = await api.updatePresetConfiguration(presetId, values);
			if (!response.success || !response.data) {
				throw new Error(response.message || 'Could not save configuration');
			}
			entries = response.data.entries || entries;
			originalValues = valuesFrom(entries);
			pendingValues = valuesFrom(entries);
			invalidatePresets();
			toasts.success('Configuration saved');
		} catch (error) {
			logger.error('Failed to save preset configuration:', error);
			toasts.error(error instanceof Error ? error.message : 'Could not save configuration');
		} finally {
			saving = false;
		}
	}

	export function discard() {
		pendingValues = valuesFrom(entries);
	}
</script>

<div class="space-y-4">
	{#if loading}
		<div class="flex items-center justify-center py-10">
			<Spinner size="md" />
		</div>
	{:else if loadError}
		<EmptyState title="Configuration unavailable" description={loadError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={load}>Try again</Button>{/snippet}
		</EmptyState>
	{:else if entries.length === 0}
		<EmptyState title="No configuration" description="This preset does not declare any configuration entries." icon="sliders" compact />
	{:else}
		{#each entries as entry (entry.key)}
			<DetailSection label={entry.label}>
				{#if entry.description}
					<p class="text-xs text-fg-muted mb-3">{entry.description}</p>
				{/if}

				{#if entry.type === 'model_tags'}
					<TagSelector
						tagType="MODEL"
						selectedTagIds={tagIdsFor(entry.key)}
						placeholder="Select model tags…"
						on:change={(event) => handleTagsChange(entry.key, event)}
					/>
				{:else}
					<div class="{DETAIL_INSET_CLASS} flex items-center gap-2 px-3 py-2 text-xs text-fg-subtle">
						<Icon name="info" className="w-3.5 h-3.5 flex-shrink-0" />
						Unsupported configuration type "{entry.type}".
					</div>
				{/if}
			</DetailSection>
		{/each}
	{/if}
</div>
