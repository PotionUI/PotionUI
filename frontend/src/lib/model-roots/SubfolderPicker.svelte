<script lang="ts">
	import { Button, IconButton, Input, Alert, Badge, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { ModelRootBrowseResult } from '$lib/services/api/models';
	import { ASSIGNABLE_MODEL_TYPES } from '$lib/utils/modelTypeControl';
	import { modelTypePresentation } from '$lib/utils/modelPresentation';
	import { browseModelRoot } from './state.svelte';
	import { breadcrumbs, subdirLabel, validateManualFolder, type FolderRef } from './logic';

	let {
		rootPath,
		existing,
		caseInsensitive = false,
		disabled = false,
		idPrefix,
		onAdd
	}: {
		rootPath: string;
		existing: readonly FolderRef[];
		caseInsensitive?: boolean;
		disabled?: boolean;
		idPrefix: string;
		onAdd: (folder: { model_type: string; subdir: string }) => Promise<string | null> | string | null;
	} = $props();

	let open = $state(false);
	let modelType = $state(ASSIGNABLE_MODEL_TYPES[0]);
	let typed = $state('');
	let rootChosen = $state(false);
	let browseSub = $state('');
	let listing = $state<ModelRootBrowseResult | null>(null);
	let loading = $state(false);
	let browseError = $state<string | null>(null);
	let adding = $state(false);
	let addError = $state<string | null>(null);

	const typeLabel = $derived(modelTypePresentation(modelType).label);
	const hasChoice = $derived(typed.trim() !== '' || rootChosen);
	const check = $derived(hasChoice ? validateManualFolder(typed, modelType, existing, caseInsensitive) : null);
	const crumbs = $derived(breadcrumbs(browseSub));
	const canAdd = $derived(!disabled && !adding && !!check && check.ok);

	async function load(sub: string) {
		loading = true;
		browseError = null;
		const result = await browseModelRoot(rootPath, sub || undefined);
		loading = false;
		if (result.listing) {
			listing = result.listing;
			browseSub = result.listing.sub;
		} else {
			listing = null;
			browseError = result.error;
		}
	}

	function openPicker() {
		open = true;
		typed = '';
		rootChosen = false;
		addError = null;
		void load('');
	}

	function closePicker() {
		open = false;
		listing = null;
		addError = null;
	}

	function enter(sub: string) {
		typed = sub;
		rootChosen = false;
		addError = null;
		void load(sub);
	}

	function chooseRoot() {
		typed = '';
		rootChosen = true;
		addError = null;
	}

	function onTyped() {
		rootChosen = false;
		addError = null;
	}

	async function submit() {
		if (!check || !check.ok) return;
		adding = true;
		addError = null;
		try {
			const error = await onAdd({ model_type: modelType, subdir: check.subdir });
			if (error) addError = error;
			else closePicker();
		} finally {
			adding = false;
		}
	}
</script>

{#if !open}
	<Button variant="secondary" size="sm" icon="folder-plus" {disabled} onclick={openPicker}>
		Add a folder for a type
	</Button>
{:else}
	<div class="w-full space-y-3 rounded-lg border border-line bg-surface-2 p-3 sm:min-w-[24rem]">
		<div class="flex items-center justify-between gap-2">
			<span class="text-sm font-medium text-fg">Add a folder for a type</span>
			<Tooltip text="Close" position="top">
				<IconButton icon="close" label="Close the folder picker" size="sm" onclick={closePicker} />
			</Tooltip>
		</div>

		<div>
			<label for="{idPrefix}-type" class="mb-1 block text-xs font-medium text-fg-muted">Type</label>
			<select
				id="{idPrefix}-type"
				class="input w-full text-sm"
				bind:value={modelType}
				disabled={adding}
				onchange={() => (addError = null)}
			>
				{#each ASSIGNABLE_MODEL_TYPES as type (type)}
					<option value={type}>{modelTypePresentation(type).label}</option>
				{/each}
			</select>
		</div>

		<div class="space-y-1.5">
			<span class="block text-xs font-medium text-fg-muted">Folder</span>
			<nav class="flex flex-wrap items-center gap-x-1 gap-y-0.5 text-sm" aria-label="Folder path">
				{#each crumbs as crumb, index (crumb.sub)}
					{#if index > 0}
						<Icon name="chevron-right" className="h-3 w-3 shrink-0 text-fg-subtle" />
					{/if}
					<button
						type="button"
						class="rounded px-1 py-0.5 {index === crumbs.length - 1 ? 'text-fg font-medium' : 'text-fg-muted hover:text-fg'}"
						disabled={loading || adding}
						onclick={() => enter(crumb.sub)}
					>
						{crumb.label}
					</button>
				{/each}
			</nav>

			<div class="max-h-60 overflow-y-auto rounded border border-line bg-surface-1">
				{#if loading}
					<div class="flex items-center gap-2 px-3 py-3 text-sm text-fg-muted"><Spinner size="sm" /> Loading folders</div>
				{:else if browseError}
					<p class="px-3 py-3 text-sm text-danger">{browseError}</p>
				{:else if listing}
					{#if listing.folders.length === 0}
						<p class="px-3 py-3 text-sm text-fg-subtle">No subfolders here.</p>
					{:else}
						<ul class="divide-y divide-line">
							{#each listing.folders as folder (folder.subdir)}
								<li>
									<button
										type="button"
										class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-fg hover:bg-surface-2"
										disabled={adding}
										onclick={() => enter(folder.subdir)}
									>
										<Icon name="folder" className="h-4 w-4 shrink-0 text-fg-muted" />
										<span class="min-w-0 flex-1 truncate">{folder.name}</span>
										{#if folder.linked}
											<Badge variant="neutral" size="sm">Link</Badge>
										{/if}
										{#if !folder.has_models}
											<Badge variant="neutral" size="sm">Empty</Badge>
										{/if}
										<Icon name="chevron-right" className="h-3 w-3 shrink-0 text-fg-subtle" />
									</button>
								</li>
							{/each}
						</ul>
						{#if listing.truncated}
							<p class="border-t border-line px-3 py-2 text-xs text-fg-subtle">Only the first folders are shown. Type the path to reach the others.</p>
						{/if}
					{/if}
				{/if}
			</div>
		</div>

		<div>
			<label for="{idPrefix}-path" class="mb-1 block text-xs font-medium text-fg-muted">Or type a path inside the folder</label>
			<Input
				id="{idPrefix}-path"
				bind:value={typed}
				placeholder="models/extra-loras"
				disabled={adding}
				invalid={!!check && !check.ok}
				oninput={onTyped}
				onkeydown={(e: KeyboardEvent) => {
					if (e.key === 'Enter' && canAdd) void submit();
				}}
			/>
			{#if check && !check.ok}
				<p class="mt-1 text-xs text-danger">{check.error}</p>
			{:else if check && check.ok}
				<p class="mt-1 text-xs text-fg-subtle">
					{typeLabel} will use <span class="font-mono">{subdirLabel(check.subdir)}</span>.
				</p>
			{:else}
				<p class="mt-1 text-xs text-fg-subtle">
					Pick a folder above, or
					<button type="button" class="text-fg underline" onclick={chooseRoot}>use the folder itself</button>.
				</p>
			{/if}
		</div>

		{#if addError}
			<Alert variant="danger" density="compact" icon>{addError}</Alert>
		{/if}

		<div class="flex items-center gap-2">
			<Button variant="primary" size="sm" icon="plus" loading={adding} disabled={!canAdd} onclick={submit}>
				Add {typeLabel} folder
			</Button>
			<Button variant="secondary" size="sm" disabled={adding} onclick={closePicker}>Cancel</Button>
		</div>
	</div>
{/if}
