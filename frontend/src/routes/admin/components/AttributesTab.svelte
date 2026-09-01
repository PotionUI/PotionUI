<!--
	Admin "Attributes" tab: manage the DB-backed AttributeDefinition rows that
	drive ModelAttributesCard (frontend/src/lib/components/modals/model-details/).
	Same idiom as BackendsTab.svelte - a searchable master list plus a detail
	pane that edits the selected definition directly, with a create-only modal.
	A `system` definition (trigger words included) locks key/field_type and
	can't be deleted, enforced both here (disabled controls) and server-side.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { logger, getErrorMessage, getApiErrorMessage } from '$lib/utils/logger';
	import { Button, Badge, Spinner, EmptyState, IconButton } from '$lib/components/ui';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import AttributeDefinitionForm from './AttributeDefinitionForm.svelte';
	import { MasterDetailLayout, DetailEmptyState } from '$lib/components/master-detail';
	import { Pane, PaneRow, PaneGroupHeader } from '$lib/components/pane';
	import { DetailHeader, DetailBody, DetailFooter } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import type { AttributeDefinition } from '$lib/types/models';
	import {
		buildAttributeDefinitionPayload,
		draftFromDefinition,
		emptyAttributeDraft,
		type AttributeDraft
	} from './attributeDefinitionForm';

	let definitions: AttributeDefinition[] = [];
	let modelTypeOptions: string[] = [];
	let loading = true;
	let error: string | null = null;
	let searchQuery = '';
	let selectedId: string | null = null;

	let showCreateModal = false;
	let createDraft: AttributeDraft = emptyAttributeDraft();
	let creating = false;

	let editDraft: AttributeDraft = emptyAttributeDraft();
	let editSnapshot = JSON.stringify(editDraft);
	let editSaving = false;

	let showDeleteModal = false;
	let deleteTarget: AttributeDefinition | null = null;
	let deleting = false;

	$: activeDefinition = definitions.find((d) => d.id === selectedId) ?? null;
	$: editDirty = JSON.stringify(editDraft) !== editSnapshot;

	$: filteredDefinitions = definitions.filter((d) => {
		const q = searchQuery.trim().toLowerCase();
		if (!q) return true;
		return d.key.toLowerCase().includes(q) || d.label.toLowerCase().includes(q);
	});
	$: systemDefinitions = filteredDefinitions.filter((d) => d.system);
	$: customDefinitions = filteredDefinitions.filter((d) => !d.system);

	$: totalCount = definitions.length;
	$: systemCount = definitions.filter((d) => d.system).length;
	$: perUserCount = definitions.filter((d) => d.per_user).length;

	async function loadDefinitions() {
		loading = true;
		error = null;
		try {
			const response = await api.getAttributeDefinitions();
			if (response.success && response.data) {
				definitions = response.data.definitions;
			} else {
				error = response.error || response.message || 'Failed to load attributes';
			}
		} catch (e: unknown) {
			error = getApiErrorMessage(e, 'Failed to load attributes');
		} finally {
			loading = false;
		}
	}

	async function loadModelTypes() {
		try {
			const response = await api.getModelTypes({ include_empty: true });
			if (response.success && response.data) {
				modelTypeOptions = response.data.types.map((t) => t.type).sort();
			}
		} catch (e: unknown) {
			logger.warn('Failed to load model types:', getErrorMessage(e));
		}
	}

	onMount(async () => {
		await Promise.all([loadDefinitions(), loadModelTypes()]);
	});

	function openCreateModal() {
		createDraft = emptyAttributeDraft();
		showCreateModal = true;
	}

	function closeCreateModal() {
		showCreateModal = false;
	}

	async function saveCreate() {
		creating = true;
		try {
			const payload = buildAttributeDefinitionPayload(createDraft);
			const response = await api.createAttributeDefinition(payload);
			if (response.success) {
				await loadDefinitions();
				closeCreateModal();
				const created = response.data?.definition;
				if (created?.id) selectDefinition(created.id);
				toasts.success(`Attribute "${payload.label}" created`);
			} else {
				toasts.error(response.error || response.message || 'Failed to create attribute');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to create attribute'));
		} finally {
			creating = false;
		}
	}

	function loadEditForm(definition: AttributeDefinition | null) {
		editDraft = definition ? draftFromDefinition(definition) : emptyAttributeDraft();
		editSnapshot = JSON.stringify(editDraft);
	}

	async function selectDefinition(id: string) {
		if (
			editDirty &&
			!(await confirmDialog({
				title: 'Discard unsaved changes',
				message: 'Discard unsaved changes to this attribute?',
				variant: 'warning'
			}))
		)
			return;
		selectedId = id;
		loadEditForm(definitions.find((d) => d.id === id) ?? null);
	}

	function discardEditForm() {
		loadEditForm(activeDefinition);
	}

	async function saveEditForm() {
		if (!activeDefinition) return;
		editSaving = true;
		try {
			const payload = buildAttributeDefinitionPayload(editDraft);
			const response = await api.updateAttributeDefinition(activeDefinition.id, payload);
			if (response.success) {
				toasts.success(`"${payload.label}" updated`);
				await loadDefinitions();
				loadEditForm(definitions.find((d) => d.id === activeDefinition!.id) ?? null);
			} else {
				toasts.error(response.error || response.message || 'Failed to save attribute');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to save attribute'));
		} finally {
			editSaving = false;
		}
	}

	function openDeleteModal(definition: AttributeDefinition) {
		deleteTarget = definition;
		showDeleteModal = true;
	}

	function closeDeleteModal() {
		showDeleteModal = false;
		deleteTarget = null;
	}

	async function deleteDefinition() {
		if (!deleteTarget) return;
		deleting = true;
		try {
			const response = await api.deleteAttributeDefinition(deleteTarget.id);
			if (response.success) {
				const wasSelected = selectedId === deleteTarget.id;
				await loadDefinitions();
				closeDeleteModal();
				if (wasSelected) {
					selectedId = null;
					loadEditForm(null);
				}
			} else {
				toasts.error(response.error || response.message || 'Failed to delete attribute');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to delete attribute'));
		} finally {
			deleting = false;
		}
	}

	function fieldTypeIcon(fieldType: AttributeDefinition['field_type']): string {
		const icons: Record<string, string> = {
			slider: 'sliders',
			number: 'hash',
			text: 'edit',
			select: 'list',
			checkbox: 'check',
			tags: 'tag'
		};
		return icons[fieldType] || 'settings';
	}
</script>

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell
		title="Attributes"
		icon="sliders"
		counts={[
			{ label: 'attributes', value: totalCount },
			{ label: 'built-in', value: systemCount, tone: 'info' },
			{ label: 'per-user', value: perUserCount }
		]}
	>
		{#snippet actions()}
			<Button variant="primary" size="sm" icon="plus" onclick={openCreateModal}>
				New attribute
			</Button>
		{/snippet}
	</AdminTabShell>

	{#snippet attributeSearch()}
		<div class="relative">
			<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
			<input
				bind:value={searchQuery}
				type="search"
				class="input pl-9"
				placeholder="Search by key or label…"
				aria-label="Search attributes"
			/>
		</div>
	{/snippet}
	{#snippet attributeSearchTrailing()}
		<span class="text-sm text-fg-muted whitespace-nowrap font-mono tabular-nums">
			{filteredDefinitions.length} {filteredDefinitions.length === 1 ? 'attribute' : 'attributes'}
		</span>
	{/snippet}

	<AdminFilterBar
		search={attributeSearch}
		trailing={attributeSearchTrailing}
		activeCount={searchQuery ? 1 : 0}
		onClear={() => (searchQuery = '')}
	/>

	<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		{#if loading}
			<div class="h-full flex flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading attributes…</p>
			</div>
		{:else if error}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState title="Error loading attributes" description={error ?? ''} icon="warning" compact>
					{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={loadDefinitions}>Try again</Button>{/snippet}
				</EmptyState>
			</div>
		{:else if definitions.length === 0}
			<div class="h-full p-5 flex items-center justify-center">
				<EmptyState
					icon="settings"
					title="No attributes defined yet"
					description="Attributes are the per-model-type fields shown in the model details card — trigger words, LoRA strength, and anything else you declare. Add one to get started."
					compact
				>
					{#snippet actions()}
						<Button variant="primary" size="sm" icon="plus" onclick={openCreateModal}>New attribute</Button>
					{/snippet}
				</EmptyState>
			</div>
		{:else}
			<MasterDetailLayout leftWidth={340} minWidth={280} maxWidth={480} storageKey="admin-attributes-width">
				<div slot="list" class="h-full min-h-0">
					<Pane
						label="Attributes"
						count={filteredDefinitions.length}
						isEmpty={filteredDefinitions.length === 0}
						bodyRole="listbox"
						ariaLabel="Attributes"
					>
						{#snippet empty()}
							<div class="p-4 h-full flex items-center justify-center">
								<EmptyState title="No attributes match your search" description="Try a different key or label." icon="search" compact>
									{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => (searchQuery = '')}>Clear search</Button>{/snippet}
								</EmptyState>
							</div>
						{/snippet}

						{#snippet children()}
							{#if systemDefinitions.length > 0}
								<PaneGroupHeader label="Built-in" count={systemDefinitions.length} />
								{#each systemDefinitions as definition (definition.id)}
									{#snippet defLeading()}<Icon name={fieldTypeIcon(definition.field_type)} className="w-4 h-4 text-fg-subtle" />{/snippet}
									{#snippet defBadges()}
										{#if definition.per_user}<Badge variant="signal" size="sm">Per-user</Badge>{/if}
										{#if definition.admin_only}<Badge variant="neutral" size="sm">Admin-only</Badge>{/if}
									{/snippet}
									<PaneRow
										selected={selectedId === definition.id}
										onclick={() => selectDefinition(definition.id)}
										leading={defLeading}
										title={definition.label}
										subtitle={definition.key}
										subtitleMono
										badges={defBadges}
									/>
								{/each}
							{/if}
							{#if customDefinitions.length > 0}
								<PaneGroupHeader label="Custom" count={customDefinitions.length} />
								{#each customDefinitions as definition (definition.id)}
									{#snippet defLeading()}<Icon name={fieldTypeIcon(definition.field_type)} className="w-4 h-4 text-fg-subtle" />{/snippet}
									{#snippet defBadges()}
										{#if definition.per_user}<Badge variant="signal" size="sm">Per-user</Badge>{/if}
										{#if definition.admin_only}<Badge variant="neutral" size="sm">Admin-only</Badge>{/if}
									{/snippet}
									<PaneRow
										selected={selectedId === definition.id}
										onclick={() => selectDefinition(definition.id)}
										leading={defLeading}
										title={definition.label}
										subtitle={definition.key}
										subtitleMono
										badges={defBadges}
									/>
								{/each}
							{/if}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full min-h-0 flex flex-col">
					{#if activeDefinition}
						<DetailHeader title={activeDefinition.label}>
							{#snippet chips()}
								<Badge variant="neutral" size="sm" class="font-mono">{activeDefinition.key}</Badge>
								<Badge variant="neutral" size="sm" class="uppercase">{activeDefinition.field_type}</Badge>
								{#if activeDefinition.system}<Badge variant="info" size="sm">Built-in</Badge>{/if}
							{/snippet}
							{#snippet actions()}
								<Tooltip text={activeDefinition.system ? "Built-in attributes can't be deleted." : 'Delete attribute'}>
									<IconButton
										icon="trash"
										label="Delete attribute"
										class="text-danger hover:text-danger hover:bg-danger/10"
										disabled={activeDefinition.system}
										onclick={() => activeDefinition && openDeleteModal(activeDefinition)}
									/>
								</Tooltip>
							{/snippet}
						</DetailHeader>

						<DetailBody>
							{#key activeDefinition.id}
								<AttributeDefinitionForm
									bind:draft={editDraft}
									layout="panel"
									idPrefix="edit-attr"
									locked={activeDefinition.system}
									{modelTypeOptions}
								/>
							{/key}
						</DetailBody>

						<DetailFooter dirtyCount={editDirty ? 1 : 0} dirtyLabel={editDirty ? 'Unsaved changes' : undefined}>
							<Button variant="ghost" size="sm" disabled={!editDirty} onclick={discardEditForm}>Discard</Button>
							<Button variant="primary" size="sm" loading={editSaving} disabled={!editDirty} onclick={saveEditForm}>Save</Button>
						</DetailFooter>
					{:else}
						<DetailEmptyState message="Select an attribute to view its details" icon="document" />
					{/if}
				</div>
			</MasterDetailLayout>
		{/if}
	</section>
</div>

<!-- Create Attribute Modal (create only — editing happens directly in the detail pane) -->
<BaseModal isOpen={showCreateModal} title="New Attribute" sizeClass="md:max-w-2xl md:w-full" on:close={closeCreateModal}>
	<div class="px-6 py-4">
		<AttributeDefinitionForm bind:draft={createDraft} layout="plain" idPrefix="create-attr" locked={false} {modelTypeOptions} />
	</div>

	<svelte:fragment slot="footer">
		<div class="px-6 py-4 flex gap-3">
			<Button
				variant="primary"
				class="flex-1"
				loading={creating}
				disabled={!createDraft.key.trim() || !createDraft.label.trim()}
				onclick={saveCreate}
			>
				{creating ? 'Creating…' : 'Create Attribute'}
			</Button>
			<Button variant="secondary" onclick={closeCreateModal}>Cancel</Button>
		</div>
	</svelte:fragment>
</BaseModal>

<!-- Delete Confirmation Modal -->
<ConfirmModal
	isOpen={showDeleteModal && !!deleteTarget}
	title="Delete Attribute"
	message={deleteTarget
		? `Are you sure you want to delete the attribute "${deleteTarget.label}"? Any stored values under its key are orphaned, not removed. This action cannot be undone.`
		: ''}
	variant="danger"
	busy={deleting}
	on:confirm={deleteDefinition}
	on:cancel={closeDeleteModal}
/>
