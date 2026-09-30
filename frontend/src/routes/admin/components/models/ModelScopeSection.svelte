<script lang="ts">
	import * as adminApi from '$lib/services/admin-api';
	import type { CloudModelScope } from '$lib/services/admin-api';
	import { Alert, Badge, Button, IconButton, Spinner } from '$lib/components/ui';
	import { modelRootsErrorMessage } from '$lib/model-roots/errors';
	import { DetailSection } from '$lib/components/detail';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import {
		addScopePreset,
		removeScopePreset,
		scopeDraftIsDirty,
		scopeOptions,
		scopePayload,
		scopeRows
	} from './modelScope';

	let {
		modelId,
		dirty = $bindable(false)
	}: {
		modelId: string;
		dirty?: boolean;
	} = $props();

	let scope = $state<CloudModelScope | null>(null);
	let draft = $state<string[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let saveError = $state<string | null>(null);
	let pickerKey = $state(0);
	let loadedFor: string | null = null;
	let requestVersion = 0;

	const rows = $derived(scope ? scopeRows(draft, scope) : []);
	const options = $derived(scope ? scopeOptions(scope, draft) : []);

	$effect(() => {
		dirty = scope ? scopeDraftIsDirty(draft, scope.preset_ids) : false;
	});

	$effect(() => {
		if (loadedFor === modelId) return;
		loadedFor = modelId;
		void load();
	});

	async function load() {
		const version = ++requestVersion;
		loading = true;
		loadError = null;
		saveError = null;
		scope = null;
		draft = [];
		try {
			const response = await adminApi.getCloudModelScope(modelId);
			if (version !== requestVersion) return;
			if (response.success && response.data) {
				scope = response.data;
				draft = [...response.data.preset_ids];
			} else {
				loadError = response.message || 'The preset scope could not be loaded.';
			}
		} catch (error) {
			if (version !== requestVersion) return;
			loadError = modelRootsErrorMessage(error, 'The preset scope could not be loaded.');
		} finally {
			if (version === requestVersion) loading = false;
		}
	}

	function add(event: CustomEvent<string>) {
		if (event.detail) draft = addScopePreset(draft, event.detail);
		pickerKey += 1;
		saveError = null;
	}

	function remove(id: string) {
		draft = removeScopePreset(draft, id);
		saveError = null;
	}

	export function discard() {
		if (scope) draft = [...scope.preset_ids];
		saveError = null;
	}

	export async function commit(): Promise<boolean> {
		if (!scope || !dirty) return true;
		saveError = null;
		try {
			const response = await adminApi.setCloudModelScope(modelId, scopePayload(draft, scope));
			if (response.success && response.data) {
				scope = response.data;
				draft = [...response.data.preset_ids];
				return true;
			}
			saveError = response.message || 'The preset scope could not be saved.';
		} catch (error) {
			saveError = modelRootsErrorMessage(error, 'The preset scope could not be saved.');
		}
		return false;
	}
</script>

<DetailSection label="Allowed in presets">
	{#snippet headerExtra()}
		{#if rows.length > 0}
			<Badge size="sm" class="font-mono tabular-nums">{rows.length}</Badge>
		{/if}
	{/snippet}
	{#if loading}
		<div class="flex items-center gap-2 text-sm text-fg-subtle" role="status">
			<Spinner size="sm" />
			<span>Loading presets…</span>
		</div>
	{:else if loadError}
		<div class="space-y-2">
			<Alert variant="danger" density="compact" icon>{loadError}</Alert>
			<Button variant="secondary" size="sm" icon="refresh" onclick={load}>Retry</Button>
		</div>
	{:else if scope}
		<div class="space-y-3">
			<p class="text-sm text-fg-muted">
				With no presets chosen, this model is offered in every compatible preset. Choose presets to limit it to those.
			</p>

			{#if rows.length > 0}
				<ul class="divide-y divide-line rounded border border-line bg-surface-2/40" aria-label="Presets this model is limited to">
					{#each rows as row (row.id)}
						<li class="flex items-center gap-2 py-1 pl-2.5 pr-1" data-scope-row={row.id}>
							<Tooltip text={row.missing ? `${row.id} (file no longer exists)` : row.id} wrapperClass="min-w-0 flex-1">
								<span class="block truncate text-sm {row.missing ? 'font-mono text-fg-subtle' : 'text-fg'}">{row.title}</span>
							</Tooltip>
							{#if row.missing}
								<Badge size="sm" variant="warning">missing</Badge>
							{:else if !row.compatible}
								<Badge size="sm" variant="warning">incompatible</Badge>
							{/if}
							<Tooltip text={`Remove ${row.title}`}>
								<IconButton icon="close" size="xs" label={`Remove ${row.title}`} onclick={() => remove(row.id)} />
							</Tooltip>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="rounded border border-dashed border-line px-3 py-3 text-center text-sm text-fg-subtle" data-scope-empty>
					No presets chosen. Offered in every compatible preset.
				</p>
			{/if}

			{#if scope.candidates.length === 0}
				<p class="text-sm text-fg-subtle">No cloud presets use this model's provider yet.</p>
			{:else}
				{#key pickerKey}
					<CustomSelect
						value=""
						{options}
						searchable
						size="sm"
						placeholder={options.length === 0 ? 'Every compatible preset is chosen' : 'Add a preset…'}
						disabled={options.length === 0}
						on:change={add}
					/>
				{/key}
			{/if}

			{#if saveError}
				<Alert variant="danger" density="compact" icon>{saveError}</Alert>
			{/if}
		</div>
	{/if}
</DetailSection>
