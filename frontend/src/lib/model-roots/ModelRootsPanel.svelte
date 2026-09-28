<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { Button, IconButton, Badge, Input, Switch, Spinner, Alert } from '$lib/components/ui';
	import { DetailSection, DETAIL_INSET_CLASS } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { formatBytes, formatCount } from '$lib/utils/format';
	import { moveItem } from '$lib/utils/reorder';
	import { ModelRootsState } from './state.svelte';
	import { orderedRoots, bindingsSummary } from './logic';
	import { ROOT_STATE_BADGE } from './format';
	import { indexingStatusStore } from '$lib/models-location/indexingStatus.svelte';
	import { indexingIsVisible } from '$lib/models-location/indexingDisplay';
	import IndexingStatusPanel from '$lib/models-location/IndexingStatusPanel.svelte';
	import AddRootModal from './AddRootModal.svelte';
	import type { ModelRoot, ModelRootBinding } from '$lib/services/api/models';

	const roots = new ModelRootsState();

	let expandedRootId = $state<string | null>(null);
	let labelDrafts = $state<Record<string, string>>({});
	let pathDrafts = $state<Record<string, string>>({});
	let savingRootId = $state<string | null>(null);
	let probingRootId = $state<string | null>(null);
	let deletingRootId = $state<string | null>(null);
	let removingBinding = $state<string | null>(null);
	let togglingReadOnly = $state<string | null>(null);
	let settingWrite = $state<string | null>(null);
	let reorderingType = $state<string | null>(null);
	let writeErrors = $state<Record<string, string>>({});

	let addModalOpen = $state(false);
	let addModalInitialPath = $state('');

	$effect(() => {
		const unsubscribe = untrack(() => indexingStatusStore.subscribe());
		return unsubscribe;
	});

	onMount(() => {
		void roots.load();
	});


	function toggleExpand(root: ModelRoot) {
		if (expandedRootId === root.id) {
			expandedRootId = null;
			return;
		}
		expandedRootId = root.id;
		labelDrafts = { ...labelDrafts, [root.id]: root.label };
		pathDrafts = { ...pathDrafts, [root.id]: root.path };
	}

	function openAddModal(initialPath: string = '') {
		addModalInitialPath = initialPath;
		addModalOpen = true;
	}

	function handleCreated(root: ModelRoot) {
		toasts.success(`Added "${root.label}".`);
		expandedRootId = root.id;
	}

	async function saveRoot(root: ModelRoot) {
		const nextLabel = labelDrafts[root.id]?.trim();
		const nextPath = pathDrafts[root.id]?.trim();
		const payload: { label?: string; path?: string } = {};
		if (nextLabel && nextLabel !== root.label) payload.label = nextLabel;
		if (nextPath && nextPath !== root.path) payload.path = nextPath;
		if (!payload.label && !payload.path) return;
		savingRootId = root.id;
		try {
			const updated = await roots.update(root.id, payload);
			if (updated) toasts.success('Folder updated.');
			else toasts.error(roots.error ?? 'Failed to update the folder.');
		} catch {
			toasts.error(roots.error ?? 'Failed to update the folder.');
		} finally {
			savingRootId = null;
		}
	}

	async function toggleReadOnly(root: ModelRoot) {
		togglingReadOnly = root.id;
		try {
			await roots.update(root.id, { read_only: !root.read_only });
		} catch {
			toasts.error(roots.error ?? 'Failed to update the folder.');
		} finally {
			togglingReadOnly = null;
		}
	}

	async function probeRoot(root: ModelRoot) {
		probingRootId = root.id;
		await roots.probe(root.id);
		probingRootId = null;
	}

	async function removeBindingType(root: ModelRoot, binding: ModelRootBinding) {
		const confirmed = await confirmDialog({
			title: 'Remove this type from the folder?',
			message: `Models under "${binding.folder}" in "${root.label}" become unavailable until you point that type at another folder. Files on disk are not touched.`,
			variant: 'danger'
		});
		if (!confirmed) return;
		removingBinding = `${root.id}:${binding.model_type}`;
		try {
			await roots.update(root.id, { remove_types: [binding.model_type] });
			toasts.success(`Removed ${binding.folder} from "${root.label}".`);
		} catch {
			toasts.error(roots.error ?? 'Failed to remove that type.');
		} finally {
			removingBinding = null;
		}
	}

	async function deleteRoot(root: ModelRoot) {
		const confirmed = await confirmDialog({
			title: 'Delete this folder?',
			message: `Models in "${root.label}" become unavailable, not deleted - the files stay on disk. You can add this folder again later.`,
			variant: 'danger'
		});
		if (!confirmed) return;
		deletingRootId = root.id;
		try {
			const ok = await roots.remove(root.id);
			if (ok) {
				toasts.success(`Deleted "${root.label}".`);
				if (expandedRootId === root.id) expandedRootId = null;
			} else {
				toasts.error(roots.error ?? 'Failed to delete the folder.');
			}
		} catch {
			toasts.error(roots.error ?? 'Failed to delete the folder.');
		} finally {
			deletingRootId = null;
		}
	}

	async function setWriteRoot(modelType: string, rootId: string) {
		const key = `${modelType}:${rootId}`;
		settingWrite = key;
		writeErrors = { ...writeErrors, [modelType]: '' };
		try {
			await roots.setWrite(rootId, modelType);
		} catch {
			writeErrors = { ...writeErrors, [modelType]: roots.error ?? 'Failed to set the write folder.' };
		} finally {
			settingWrite = null;
		}
	}

	async function moveTypeRoot(modelType: string, order: string[], index: number, delta: number) {
		reorderingType = modelType;
		try {
			await roots.reorder(moveItem(order, index, index + delta), modelType);
		} catch {
			toasts.error(roots.error ?? 'Failed to reorder folders.');
		} finally {
			reorderingType = null;
		}
	}
</script>

<div class="space-y-4">
	{#if roots.loading}
		<Spinner size="sm" />
	{:else}
		{#if roots.error && !roots.overview}
			<Alert variant="danger" icon>{roots.error}</Alert>
		{/if}

		{#if indexingIsVisible(indexingStatusStore.status)}
			<DetailSection label="Model indexing">
				<IndexingStatusPanel status={indexingStatusStore.status} />
			</DetailSection>
		{/if}

		<DetailSection label="Model folders" padded={false}>
			{#snippet headerExtra()}
				<Button variant="secondary" size="sm" icon="folder-plus" onclick={() => openAddModal()}>
					Add folder
				</Button>
			{/snippet}

			<ul class="divide-y divide-line">
				{#each orderedRoots(roots.roots) as root (root.id)}
					{@const summary = bindingsSummary(root)}
					{@const badge = ROOT_STATE_BADGE[root.state]}
					<li>
						<button
							type="button"
							class="w-full flex items-center gap-3 px-4 sm:px-5 py-3 text-left hover:bg-surface-2"
							onclick={() => toggleExpand(root)}
							aria-expanded={expandedRootId === root.id}
						>
							<Icon
								name={root.kind === 'home' ? 'folder' : 'server'}
								className="w-4 h-4 text-fg-muted flex-shrink-0"
							/>
							<div class="min-w-0 flex-1">
								<div class="flex items-center gap-2 flex-wrap">
									<span class="text-sm text-fg font-medium truncate">{root.label}</span>
									{#if root.kind === 'home'}
										<Badge variant="neutral" size="sm">Built-in</Badge>
									{/if}
									{#if root.read_only}
										<Badge variant="warning" size="sm">Read-only</Badge>
									{/if}
									<Tooltip text={root.state_reason ?? badge.label} position="top">
										<Badge variant={badge.variant} size="sm" dot>{badge.label}</Badge>
									</Tooltip>
								</div>
								<span class="block truncate font-mono text-xs text-fg-subtle">{root.path}</span>
							</div>
							<span class="shrink-0 font-mono text-xs tabular-nums text-fg-muted text-right">
								{formatCount(summary.files)} file{summary.files === 1 ? '' : 's'}
								<span class="block text-fg-subtle">{formatBytes(summary.bytes)}</span>
							</span>
							<Icon
								name="chevron-down"
								className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0 transition-transform {expandedRootId === root.id ? '' : '-rotate-90'}"
							/>
						</button>

						{#if expandedRootId === root.id}
							<div class="px-4 sm:px-5 py-4 space-y-4 {DETAIL_INSET_CLASS} mx-4 sm:mx-5 mb-4">
								<div class="grid gap-3 sm:grid-cols-2">
									<div>
										<label for="root-label-{root.id}" class="block text-xs font-medium text-fg-muted mb-1">Label</label>
										<Input
											id="root-label-{root.id}"
											bind:value={labelDrafts[root.id]}
											disabled={savingRootId === root.id}
										/>
									</div>
									<div>
										<label for="root-path-{root.id}" class="block text-xs font-medium text-fg-muted mb-1">Path</label>
										<Input
											id="root-path-{root.id}"
											bind:value={pathDrafts[root.id]}
											disabled={root.kind === 'home' || savingRootId === root.id}
										/>
									</div>
								</div>
								<div class="flex items-center justify-between flex-wrap gap-2">
									<Switch
										checked={root.read_only}
										onchange={() => toggleReadOnly(root)}
										disabled={root.kind === 'home' || togglingReadOnly === root.id}
										busy={togglingReadOnly === root.id}
										label="Read-only"
										id="root-readonly-{root.id}"
									/>
									<div class="flex items-center gap-2">
										<Button
											variant="secondary"
											size="sm"
											icon="refresh"
											loading={probingRootId === root.id}
											onclick={() => probeRoot(root)}
										>
											Probe now
										</Button>
										<Button
											variant="secondary"
											size="sm"
											icon="save"
											loading={savingRootId === root.id}
											disabled={root.kind === 'home'}
											onclick={() => saveRoot(root)}
										>
											Save
										</Button>
										{#if root.kind !== 'home'}
											<Button
												variant="danger"
												size="sm"
												icon="trash-2"
												loading={deletingRootId === root.id}
												onclick={() => deleteRoot(root)}
											>
												Delete
											</Button>
										{/if}
									</div>
								</div>

								<div>
									<span class="block text-xs font-medium uppercase tracking-[0.05em] text-fg-subtle mb-1.5">
										Bindings
									</span>
									{#if root.bindings.length === 0}
										<p class="text-xs text-fg-subtle">No types bound to this folder yet.</p>
									{:else}
										<ul class="space-y-1.5">
											{#each root.bindings as binding (binding.model_type)}
												<li class="flex items-center gap-2.5 rounded border border-line bg-surface-2 px-3 py-2">
													<div class="min-w-0 flex-1">
														<div class="flex items-center gap-2">
															<span class="text-sm text-fg">{binding.folder}</span>
															{#if binding.is_write}
																<Badge variant="signal" size="sm">Write</Badge>
															{/if}
															{#if !binding.exists}
																<Tooltip text="This folder is missing on disk" position="top">
																	<Badge variant="warning" size="sm">Missing</Badge>
																</Tooltip>
															{/if}
														</div>
														<span class="block truncate font-mono text-xs text-fg-subtle">{binding.path}</span>
													</div>
													<span class="shrink-0 font-mono text-xs tabular-nums text-fg-muted">
														{formatCount(binding.indexed_files)}
													</span>
													<Tooltip text="Remove this type from the folder" position="top">
														<IconButton
															icon="trash-2"
															label="Remove {binding.folder} from {root.label}"
															size="sm"
															disabled={root.kind === 'home'}
															loading={removingBinding === `${root.id}:${binding.model_type}`}
															onclick={() => removeBindingType(root, binding)}
														/>
													</Tooltip>
												</li>
											{/each}
										</ul>
									{/if}
								</div>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		</DetailSection>

		{#if roots.overview}
			{@const orderableTypes = roots.overview.types.filter((t) => t.order.length > 1)}
			<DetailSection label="Model type order">
				{#if orderableTypes.length === 0}
					<p class="text-xs text-fg-subtle">
						Ordering appears here once a model type has more than one folder to choose between.
					</p>
				{:else}
					<div class="space-y-3">
						{#each orderableTypes as type (type.model_type)}
							<div>
								<span class="block text-xs font-medium uppercase tracking-[0.05em] text-fg-subtle mb-1.5">
									{type.folder}
								</span>
								{#if writeErrors[type.model_type]}
									<Alert variant="danger" density="compact" icon>{writeErrors[type.model_type]}</Alert>
								{/if}
								<ul class="space-y-1">
									{#each type.order as rootId, index (rootId)}
										{@const root = roots.rootById(rootId)}
										{#if root}
											{@const writeDisabledReason = root.read_only
												? 'This folder is read-only'
												: root.state !== 'online'
													? `This folder is ${root.state}`
													: null}
											<li class="flex items-center gap-2 rounded border border-line bg-surface-2 px-3 py-1.5">
												<span class="font-mono text-xs tabular-nums text-fg-subtle w-4">{index + 1}</span>
												<span class="min-w-0 flex-1 truncate text-sm text-fg">{root.label}</span>
												{#if type.write_root_id === rootId}
													<Badge variant="signal" size="sm">Write</Badge>
												{:else if writeDisabledReason}
													<Tooltip text={writeDisabledReason} position="top">
														<Button variant="ghost" size="sm" disabled>Set as write</Button>
													</Tooltip>
												{:else}
													<Button
														variant="ghost"
														size="sm"
														loading={settingWrite === `${type.model_type}:${rootId}`}
														onclick={() => setWriteRoot(type.model_type, rootId)}
													>
														Set as write
													</Button>
												{/if}
												<div class="flex items-center gap-0.5">
													<IconButton
														icon="chevron-up"
														label="Move {root.label} earlier for {type.folder}"
														size="sm"
														disabled={index === 0 || reorderingType === type.model_type}
														onclick={() => moveTypeRoot(type.model_type, type.order, index, -1)}
													/>
													<IconButton
														icon="chevron-down"
														label="Move {root.label} later for {type.folder}"
														size="sm"
														disabled={index === type.order.length - 1 || reorderingType === type.model_type}
														onclick={() => moveTypeRoot(type.model_type, type.order, index, 1)}
													/>
												</div>
											</li>
										{/if}
									{/each}
								</ul>
							</div>
						{/each}
					</div>
				{/if}
			</DetailSection>
		{/if}

		{#if roots.overview?.unplaced?.length}
			<DetailSection label="Suggested folders">
				<p class="text-xs text-fg-subtle mb-2">
					Models were found in these folders that aren't part of a root yet.
				</p>
				<ul class="space-y-1.5">
					{#each roots.overview.unplaced as entry (entry.dir)}
						<li class="flex items-center gap-2.5 rounded border border-line bg-surface-2 px-3 py-2">
							<div class="min-w-0 flex-1">
								<span class="block truncate font-mono text-xs text-fg">{entry.dir}</span>
								<span class="block text-xs text-fg-subtle">
									{formatCount(entry.count)} file{entry.count === 1 ? '' : 's'} - {entry.types.join(', ')}
								</span>
							</div>
							<Button variant="secondary" size="sm" onclick={() => openAddModal(entry.dir)}>
								Add as root
							</Button>
						</li>
					{/each}
				</ul>
			</DetailSection>
		{/if}
	{/if}
</div>

<AddRootModal
	isOpen={addModalOpen}
	rootsState={roots}
	pathStyle={roots.overview?.path_style}
	initialPath={addModalInitialPath}
	onClose={() => (addModalOpen = false)}
	onCreated={handleCreated}
/>
