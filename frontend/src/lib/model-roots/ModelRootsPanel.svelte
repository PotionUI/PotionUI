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
	import { ModelRootsState, detectModelRoot, listModelLayouts } from './state.svelte';
	import {
		orderedRoots,
		bindingsSummary,
		bindingSupportsHeaderScan,
		bindingScanKey,
		bindingRefKey,
		bindingTitle,
		buildBindings,
		detectAgainApplies,
		groupBindingsByType,
		missingSuggestions,
		profileBadgeLabel,
		suggestionKey
	} from './logic';
	import { modelTypePresentation } from '$lib/utils/modelPresentation';
	import { modelRootsErrorMessage } from './errors';
	import { ROOT_STATE_BADGE } from './format';
	import { indexingStatusStore } from '$lib/models-location/indexingStatus.svelte';
	import { indexingIsVisible } from '$lib/models-location/indexingDisplay';
	import IndexingStatusPanel from '$lib/models-location/IndexingStatusPanel.svelte';
	import AddRootModal from './AddRootModal.svelte';
	import type {
		ModelLayoutSummary,
		ModelRoot,
		ModelRootBinding,
		ModelRootDetectionSuggestion
	} from '$lib/services/api/models';

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
	let togglingScan = $state<string | null>(null);
	let scanErrors = $state<Record<string, string>>({});
	let scanRevision = $state(0);
	let catalog = $state<ModelLayoutSummary[]>([]);
	let settingDownloads = $state<string | null>(null);
	let bindingErrors = $state<Record<string, string>>({});
	let detectingAgainId = $state<string | null>(null);
	let againResults = $state<
		Record<string, { missing: ModelRootDetectionSuggestion[]; ticks: Record<string, boolean>; note: string | null }>
	>({});
	let addingMissingId = $state<string | null>(null);

	let addModalOpen = $state(false);
	let addModalInitialPath = $state('');

	$effect(() => {
		const unsubscribe = untrack(() => indexingStatusStore.subscribe());
		return unsubscribe;
	});

	onMount(() => {
		void roots.load();
		void listModelLayouts().then((layouts) => (catalog = layouts));
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

	async function toggleHeaderScan(root: ModelRoot, binding: ModelRootBinding, enabled: boolean) {
		const key = bindingScanKey(root.id, binding);
		togglingScan = key;
		scanErrors = { ...scanErrors, [key]: '' };
		try {
			await roots.setBindingScan(root.id, binding, enabled);
		} catch {
			scanErrors = { ...scanErrors, [key]: roots.error ?? 'Failed to change header detection.' };
			scanRevision += 1;
		} finally {
			togglingScan = null;
		}
	}

	async function probeRoot(root: ModelRoot) {
		probingRootId = root.id;
		await roots.probe(root.id);
		probingRootId = null;
	}

	async function removeBinding(root: ModelRoot, binding: ModelRootBinding) {
		const title = bindingTitle(binding);
		const confirmed = await confirmDialog({
			title: 'Remove this folder from the root?',
			message: `Models under "${title}" in "${root.label}" become unavailable until you point that type at another folder. Files on disk are not touched.`,
			variant: 'danger'
		});
		if (!confirmed) return;
		removingBinding = bindingRefKey(root.id, binding);
		try {
			await roots.update(root.id, { remove_bindings: [{ model_type: binding.model_type, subdir: binding.subdir }] });
			toasts.success(`Removed ${title} from "${root.label}".`);
		} catch {
			toasts.error(roots.error ?? 'Failed to remove that folder.');
		} finally {
			removingBinding = null;
		}
	}

	async function makeDownloadsFolder(root: ModelRoot, binding: ModelRootBinding) {
		const key = bindingRefKey(root.id, binding);
		settingDownloads = key;
		bindingErrors = { ...bindingErrors, [key]: '' };
		try {
			await roots.setWrite(root.id, binding.model_type, binding.subdir);
		} catch {
			bindingErrors = { ...bindingErrors, [key]: roots.error ?? 'Failed to set the downloads folder.' };
		} finally {
			settingDownloads = null;
		}
	}

	async function detectAgain(root: ModelRoot) {
		detectingAgainId = root.id;
		const { detection, error } = await detectModelRoot(root.path);
		detectingAgainId = null;
		if (!detection) {
			againResults = { ...againResults, [root.id]: { missing: [], ticks: {}, note: error } };
			return;
		}
		if (!detectAgainApplies(root, detection)) {
			againResults = {
				...againResults,
				[root.id]: { missing: [], ticks: {}, note: 'This folder belongs to a larger install, so nothing was compared.' }
			};
			return;
		}
		const missing = missingSuggestions(root, detection.suggestions);
		const ticks: Record<string, boolean> = {};
		for (const s of missing) ticks[suggestionKey(s)] = true;
		againResults = {
			...againResults,
			[root.id]: { missing, ticks, note: missing.length === 0 ? 'Nothing new found. Every detected folder is already added.' : null }
		};
	}

	async function addMissing(root: ModelRoot) {
		const result = againResults[root.id];
		if (!result) return;
		const chosen = result.missing.filter((s) => result.ticks[suggestionKey(s)]);
		if (chosen.length === 0) return;
		addingMissingId = root.id;
		try {
			await roots.update(root.id, {
				bindings: buildBindings(result.missing, result.ticks, {}, false).bindings
			});
			toasts.success(`Added ${chosen.length} folder${chosen.length === 1 ? '' : 's'} to "${root.label}".`);
			const { [root.id]: _done, ...rest } = againResults;
			againResults = rest;
		} catch (e) {
			againResults = {
				...againResults,
				[root.id]: { ...result, note: modelRootsErrorMessage(e, 'Failed to add those folders.') }
			};
		} finally {
			addingMissingId = null;
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
									{#if root.layout_profile}
										<Badge variant="neutral" size="sm">{profileBadgeLabel(root.layout_profile, catalog)}</Badge>
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
											icon="scan-search"
											loading={detectingAgainId === root.id}
											disabled={root.kind === 'home' || root.state !== 'online'}
											onclick={() => detectAgain(root)}
										>
											Detect again
										</Button>
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
										<div class="space-y-3">
											{#each groupBindingsByType(root.bindings) as group (group.model_type)}
												<div class="space-y-1.5">
													<span class="block text-sm font-medium text-fg">{modelTypePresentation(group.model_type).label}</span>
													<ul class="space-y-1.5">
														{#each group.items as binding (binding.subdir)}
												<li class="flex items-center gap-2.5 rounded border border-line bg-surface-2 px-3 py-2">
													<div class="min-w-0 flex-1">
														<div class="flex items-center gap-2">
															<span class="text-sm text-fg">{bindingTitle(binding)}</span>
															{#if binding.is_write}
																<Badge variant="signal" size="sm">Downloads</Badge>
															{/if}
															{#if !binding.exists}
																<Tooltip text="This folder is missing on disk" position="top">
																	<Badge variant="warning" size="sm">Missing</Badge>
																</Tooltip>
															{/if}
														</div>
														<span class="block truncate font-mono text-xs text-fg-subtle">{binding.path}</span>
														{#if bindingErrors[bindingRefKey(root.id, binding)]}
															<p class="mt-1 text-xs text-danger">{bindingErrors[bindingRefKey(root.id, binding)]}</p>
														{/if}
														{#if bindingSupportsHeaderScan(binding)}
															{@const scanKey = bindingScanKey(root.id, binding)}
															<div class="mt-2">
																<Tooltip
																	text="Reads the file header to tell full checkpoints from diffusion models. Pickle files keep the folder's type."
																	position="top"
																>
																	<label for="binding-scan-{scanKey}" class="inline-flex items-center gap-2 text-xs text-fg-muted">
																		{#key scanRevision}
																			<Switch
																				checked={binding.scan_headers}
																				onchange={(next) => toggleHeaderScan(root, binding, next)}
																				disabled={togglingScan === scanKey}
																				busy={togglingScan === scanKey}
																				size="sm"
																				label="Detect type from file"
																				id="binding-scan-{scanKey}"
																			/>
																		{/key}
																		Detect type from file
																	</label>
																</Tooltip>
																{#if scanErrors[scanKey]}
																	<p class="mt-1 text-xs text-danger">{scanErrors[scanKey]}</p>
																{/if}
															</div>
														{/if}
													</div>
													<span class="shrink-0 font-mono text-xs tabular-nums text-fg-muted">
														{formatCount(binding.indexed_files)}
													</span>
													{#if !binding.is_write && !root.read_only}
														<Tooltip text="Make this the downloads folder for {modelTypePresentation(binding.model_type).label}" position="top">
															<IconButton
																icon="download"
																label="Make {bindingTitle(binding)} the downloads folder"
																size="sm"
																loading={settingDownloads === bindingRefKey(root.id, binding)}
																onclick={() => makeDownloadsFolder(root, binding)}
															/>
														</Tooltip>
													{/if}
													<Tooltip text="Remove this folder from the root" position="top">
														<IconButton
															icon="trash-2"
															label="Remove {bindingTitle(binding)} from {root.label}"
															size="sm"
															disabled={root.kind === 'home'}
															loading={removingBinding === bindingRefKey(root.id, binding)}
															onclick={() => removeBinding(root, binding)}
														/>
													</Tooltip>
												</li>
														{/each}
													</ul>
												</div>
											{/each}
										</div>
									{/if}
									{#if againResults[root.id]}
										{@const again = againResults[root.id]}
										<div class="mt-3 space-y-2 rounded border border-line bg-surface-2 px-3 py-2.5">
											<span class="block text-xs font-medium uppercase tracking-[0.05em] text-fg-subtle">
												Found by detecting again
											</span>
											{#if again.note}
												<p class="text-sm text-fg-muted">{again.note}</p>
											{/if}
											{#if again.missing.length > 0}
												<ul class="space-y-1.5">
													{#each again.missing as suggestion (suggestionKey(suggestion))}
														{@const key = suggestionKey(suggestion)}
														<li class="flex items-center gap-2.5">
															<input
																type="checkbox"
																id="again-{root.id}-{key}"
																class="h-4 w-4 rounded accent-signal-solid"
																bind:checked={again.ticks[key]}
															/>
															<label for="again-{root.id}-{key}" class="min-w-0 flex-1 cursor-pointer">
																<span class="block text-sm text-fg">
																	{modelTypePresentation(suggestion.model_type).label}
																	{#if suggestion.label && suggestion.label.toLowerCase() !== modelTypePresentation(suggestion.model_type).label.toLowerCase()}
																		<span class="text-fg-muted">{suggestion.label}</span>
																	{/if}
																</span>
																<span class="block truncate font-mono text-xs text-fg-subtle">{suggestion.subdir}</span>
															</label>
														</li>
													{/each}
												</ul>
												<Button
													variant="secondary"
													size="sm"
													icon="plus"
													loading={addingMissingId === root.id}
													disabled={!again.missing.some((s) => again.ticks[suggestionKey(s)])}
													onclick={() => addMissing(root)}
												>
													Add selected
												</Button>
											{/if}
										</div>
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
				<p class="text-sm text-fg-muted mb-3">
					New downloads of a type are saved to its Write folder. When the same model is in more than one folder, the earlier folder is used. Existing files never move.
				</p>
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
													<Tooltip text="New {type.folder} downloads are saved to {root.label}." position="top">
														<Badge variant="signal" size="sm">Write</Badge>
													</Tooltip>
												{:else if writeDisabledReason}
													<Tooltip text={writeDisabledReason} position="top">
														<Button variant="ghost" size="sm" disabled>Set as write</Button>
													</Tooltip>
												{:else}
													<Tooltip
														text="New {type.folder} downloads go to {root.label}. Existing files stay where they are."
														position="top"
													>
														<Button
															variant="ghost"
															size="sm"
															loading={settingWrite === `${type.model_type}:${rootId}`}
															onclick={() => setWriteRoot(type.model_type, rootId)}
														>
															Set as write
														</Button>
													</Tooltip>
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
