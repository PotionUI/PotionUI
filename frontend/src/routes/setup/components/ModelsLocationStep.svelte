<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { toasts } from '$lib/stores/toast';
	import { Alert, Button, Card, ChoiceCard, Spinner } from '$lib/components/ui';
	import StepFacts from '$lib/components/setup/StepFacts.svelte';
	import { ModelRootsState } from '$lib/model-roots/state.svelte';
	import DetectRootForm from '$lib/model-roots/DetectRootForm.svelte';
	import { indexingStatusStore } from '$lib/models-location/indexingStatus.svelte';
	import { indexingDigests, indexingScanHasContent, setupModelsLine } from '$lib/models-location/indexingDisplay';
	import IndexingStatusPanel from '$lib/models-location/IndexingStatusPanel.svelte';
	import type { ModelRoot } from '$lib/services/api/models';
	import { formatBytes } from '$lib/utils/format';
	import { MODELS_LOCATION_DONE_KEY, readGuideFlag, writeGuideFlag } from '$lib/utils/setupGuideFlags';

	let {
		freeDiskBytes = null,
		embedded = false,
		initialPath = null,
		onContinue,
		onBack
	}: {
		freeDiskBytes?: number | null;
		embedded?: boolean;
		initialPath?: string | null;
		onContinue?: () => void;
		onBack?: () => void;
	} = $props();

	type Choice = 'fresh' | 'existing';

	const roots = new ModelRootsState();

	let dismissed = $state(false);
	let chooserOpen = $state(false);
	let choice = $state<Choice>('fresh');
	let addedRoot = $state<ModelRoot | null>(null);

	const modelsLine = $derived(setupModelsLine(indexingDigests(indexingStatusStore.status)));
	const pathLabel = $derived.by(() => {
		const library = roots.roots.find((r) => r.kind !== 'home');
		if (library) return library.path;
		const home = roots.roots.find((r) => r.kind === 'home');
		return home ? home.path : (initialPath ?? 'models/ (default location in this install)');
	});

	$effect(() => {
		const unsubscribe = untrack(() => indexingStatusStore.subscribe());
		return unsubscribe;
	});

	onMount(async () => {
		dismissed = readGuideFlag(MODELS_LOCATION_DONE_KEY);
		await roots.load();
	});

	function persistDismissed(value: boolean) {
		dismissed = value;
		writeGuideFlag(MODELS_LOCATION_DONE_KEY, value);
	}

	function handleCreated(root: ModelRoot) {
		addedRoot = root;
		if (roots.overview?.indexing) {
			indexingStatusStore.notifyRunStarted(roots.overview.indexing);
		}
		toasts.success(`Added "${root.label}". Re-indexing in the background.`);
		persistDismissed(true);
		chooserOpen = false;
		onContinue?.();
	}

	function continueFresh() {
		persistDismissed(true);
		chooserOpen = false;
		onContinue?.();
	}

	function openChooser(next: Choice) {
		choice = next;
		chooserOpen = true;
	}

	function closeChooser() {
		chooserOpen = false;
		persistDismissed(true);
		onContinue?.();
	}

	function changeChoice() {
		choice = addedRoot ? 'existing' : 'fresh';
		persistDismissed(false);
	}

	const FACTS = [
		{
			label: 'Why a folder',
			body: ['Models are too big to hide inside the app. One folder keeps them easy to find, back up or move.']
		},
		{
			label: 'Need ComfyUI?',
			body: ['No. ComfyUI, Forge and A1111 folders are just examples of folders PotionUI can reuse.']
		},
		{
			label: 'Shared with others',
			body: ['Installed models are shared: everyone with an account on this PotionUI sees the same ones.']
		}
	];
</script>

{#if embedded && !chooserOpen}
	<div class="space-y-2">
		<div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-base" data-models-row>
			<span class="min-w-0 text-fg-muted">
				Saved to <span class="break-all font-mono text-fg">{pathLabel}</span>
			</span>
			<span class="flex shrink-0 items-center gap-4">
				<button
					type="button"
					class="font-medium text-signal hover:underline"
					onclick={() => openChooser('fresh')}
				>
					Change
				</button>
				<button
					type="button"
					class="font-medium text-signal hover:underline"
					onclick={() => openChooser('existing')}
				>
					Use a folder you already have
				</button>
			</span>
		</div>
		{#if modelsLine}
			<p class="text-base text-fg-subtle">{modelsLine}</p>
		{/if}
		{#if indexingScanHasContent(indexingStatusStore.status)}
			<IndexingStatusPanel status={indexingStatusStore.status} />
		{/if}
	</div>
{:else if !embedded && dismissed}
	<div class="space-y-4">
		<Card class="space-y-1">
			<div class="flex items-center justify-between gap-3 text-base text-fg-muted">
				<span class="min-w-0">
					Models location: <span class="break-all font-mono text-fg">{pathLabel}</span>
				</span>
				<button
					type="button"
					class="shrink-0 text-sm text-fg-muted hover:text-fg"
					onclick={changeChoice}
				>
					Change
				</button>
			</div>
			{#if modelsLine}
				<p class="text-base text-fg-subtle">{modelsLine}</p>
			{/if}
		</Card>
		<StepFacts facts={FACTS} />
		<div class="flex flex-wrap items-center gap-3">
			{#if onContinue}
				<Button variant="primary" size="lg" onclick={onContinue}>Continue</Button>
			{/if}
			{#if onBack}
				<Button variant="ghost" onclick={onBack}>Back</Button>
			{/if}
		</div>
	</div>
{:else}
	<div class="space-y-5">
		<Card class="space-y-3">
			<div>
				<h3 id="models-choice-title" class="text-lg font-semibold text-fg">Choose a location</h3>
				<p class="mt-0.5 text-base text-fg-muted">You can change this later.</p>
			</div>

			<div
				class="grid grid-cols-2 gap-3 max-sm:grid-cols-1"
				role="radiogroup"
				aria-labelledby="models-choice-title"
			>
				<ChoiceCard
					checked={choice === 'fresh'}
					title="Start fresh"
					badge="Recommended"
					description="PotionUI keeps models in its own folder. Nothing else to install or set up."
					onselect={() => (choice = 'fresh')}
				/>
				<ChoiceCard
					checked={choice === 'existing'}
					title="Use an existing folder"
					description="Reuse models you already have, from ComfyUI, Forge, A1111 or any folder of model files."
					onselect={() => (choice = 'existing')}
				/>
			</div>

			{#if roots.loading}
				<Spinner size="sm" />
			{:else}
				{#if roots.error}
					<Alert variant="danger" icon>{roots.error}</Alert>
				{/if}

				{#if choice === 'fresh'}
					<div class="space-y-3 border-t border-line pt-3.5" data-panel="fresh">
						<div>
							<p class="mb-1.5 font-mono text-sm uppercase tracking-[0.06em] text-fg-subtle">
								Models will be saved to
							</p>
							<div class="flex min-w-0 items-center rounded border border-field-border bg-field-bg px-3 py-2">
								<span class="min-w-0 flex-1 truncate font-mono text-fg">{pathLabel}</span>
							</div>
						</div>
						<p class="text-base text-fg-subtle">
							{#if freeDiskBytes != null}
								<span class="font-mono tabular-nums">{formatBytes(freeDiskBytes, 0)}</span> free on that drive.
							{/if}
							Change your mind any time in
							<span class="font-medium text-fg-muted">Admin → Models → Folders</span>: add a folder you
							already have, or move to another one.
						</p>
					</div>
				{:else}
					<div class="space-y-3 border-t border-line pt-3.5" data-panel="existing">
						<DetectRootForm
							rootsState={roots}
							pathStyle={roots.overview?.path_style}
							onCreated={handleCreated}
							secondaryLabel="Back to start fresh"
							onSecondary={() => (choice = 'fresh')}
						/>
						<p class="text-base text-fg-subtle">
							PotionUI reads this folder in place. It does not copy or move your files.
						</p>
					</div>
				{/if}

				{#if indexingScanHasContent(indexingStatusStore.status)}
					<div class="border-t border-line pt-3">
						<IndexingStatusPanel status={indexingStatusStore.status} />
					</div>
				{/if}

				{#if modelsLine}
					<p class="text-base text-fg-subtle">{modelsLine}</p>
				{/if}
			{/if}
		</Card>

		{#if !embedded}
			<StepFacts facts={FACTS} />
		{/if}

		<div class="flex flex-wrap items-center gap-3">
			{#if choice === 'fresh'}
				<Button variant="primary" size={embedded ? 'md' : 'lg'} onclick={embedded ? closeChooser : continueFresh}>
					{embedded ? 'Keep this folder' : 'Continue'}
				</Button>
			{/if}
			{#if embedded}
				<Button variant="ghost" onclick={() => (chooserOpen = false)}>Close</Button>
			{:else if onBack}
				<Button variant="ghost" onclick={onBack}>Back</Button>
			{/if}
		</div>
	</div>
{/if}
