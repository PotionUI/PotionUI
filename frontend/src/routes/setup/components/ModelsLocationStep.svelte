<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { browser } from '$app/environment';
	import { toasts } from '$lib/stores/toast';
	import { Alert, Spinner, Card } from '$lib/components/ui';
	import { ModelRootsState } from '$lib/model-roots/state.svelte';
	import DetectRootForm from '$lib/model-roots/DetectRootForm.svelte';
	import { indexingStatusStore } from '$lib/models-location/indexingStatus.svelte';
	import { indexingIsRunning, indexingIsVisible } from '$lib/models-location/indexingDisplay';
	import IndexingStatusPanel from '$lib/models-location/IndexingStatusPanel.svelte';
	import type { ModelRoot } from '$lib/services/api/models';

	const DISMISSED_STORAGE_KEY = 'potionui:setup:modelsLocationDismissed';

	const roots = new ModelRootsState();

	let dismissed = $state(false);
	let addedRoot = $state<ModelRoot | null>(null);

	$effect(() => {
		const unsubscribe = untrack(() => indexingStatusStore.subscribe());
		return unsubscribe;
	});

	onMount(async () => {
		if (browser) {
			try {
				dismissed = localStorage.getItem(DISMISSED_STORAGE_KEY) === '1';
			} catch {
				// localStorage may be unavailable - the step just stays expanded.
			}
		}
		await roots.load();
	});

	function persistDismissed(value: boolean) {
		dismissed = value;
		if (!browser) return;
		try {
			if (value) {
				localStorage.setItem(DISMISSED_STORAGE_KEY, '1');
			} else {
				localStorage.removeItem(DISMISSED_STORAGE_KEY);
			}
		} catch {
			// ignore - dismissal just won't survive a reload
		}
	}

	function currentPathLabel(): string {
		const libraryRoot = roots.roots.find((r) => r.kind !== 'home');
		return libraryRoot ? libraryRoot.path : 'models/ (default location in this install)';
	}

	function handleCreated(root: ModelRoot) {
		addedRoot = root;
		if (roots.overview?.indexing) {
			indexingStatusStore.notifyRunStarted(roots.overview.indexing);
		}
		toasts.success(`Added "${root.label}". Re-indexing in the background.`);
		persistDismissed(true);
	}

	function skip() {
		persistDismissed(true);
	}
</script>

{#if dismissed}
	<div class="space-y-1">
		<div class="flex items-center justify-between gap-3 text-sm text-fg-muted">
			<span>Models location: <span class="font-mono text-fg">{currentPathLabel()}</span></span>
			<button type="button" class="text-xs text-fg-muted hover:text-fg" onclick={() => persistDismissed(false)}>
				Change
			</button>
		</div>
		{#if indexingIsRunning(indexingStatusStore.status)}
			<p class="text-xs text-fg-subtle">
				Model indexing continues in the background - you can go ahead and set up a recipe.
			</p>
		{/if}
	</div>
{:else}
	<Card class="space-y-3">
		<div>
			<h2 class="text-sm font-semibold text-fg">Models location</h2>
			<p class="text-sm text-fg-muted mt-0.5">
				Point PotionUI at a folder that already has models (a ComfyUI or A1111 install works)
				before anything downloads. You can add more folders later in Admin - Models - Folders.
			</p>
		</div>

		{#if roots.loading}
			<Spinner size="sm" />
		{:else}
			{#if roots.error}
				<Alert variant="danger" icon>{roots.error}</Alert>
			{/if}

			<p class="text-xs text-fg-subtle">
				Currently: <span class="font-mono text-fg-muted">{currentPathLabel()}</span>
			</p>

			<DetectRootForm
				rootsState={roots}
				pathStyle={roots.overview?.path_style}
				onCreated={handleCreated}
				secondaryLabel="Skip"
				onSecondary={skip}
			/>

			{#if indexingIsVisible(indexingStatusStore.status)}
				<div class="mt-2 pt-3 border-t border-line">
					<IndexingStatusPanel status={indexingStatusStore.status} />
				</div>
			{/if}
		{/if}
	</Card>
{/if}
