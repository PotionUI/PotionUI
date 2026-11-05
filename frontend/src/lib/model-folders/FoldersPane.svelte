<script lang="ts">
	import { IconButton, LoadErrorState, Spinner } from '$lib/components/ui';
	import { DetailBody, DetailHeader } from '$lib/components/detail';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { isMobile } from '$lib/stores/viewport';
	import AddRootModal from '$lib/model-roots/AddRootModal.svelte';
	import IndexingSettingsSection from '$lib/models-location/IndexingSettingsSection.svelte';
	import RootPane from './RootPane.svelte';
	import RoutingPane from './RoutingPane.svelte';
	import SuggestedPane from './SuggestedPane.svelte';
	import type { FoldersController } from './controller.svelte';

	let { ctrl, onBack }: { ctrl: FoldersController; onBack: () => void } = $props();

	const ov = $derived(ctrl.overview);
	const effective = $derived(ctrl.effective);
	const root = $derived(ctrl.sortedRoots.find((r) => r.id === effective));
	const selectItems = $derived([
		...ctrl.sortedRoots.map((r) => ({ id: r.id, label: r.label })),
		{ id: 'routing', label: 'Downloads & priority' },
		{ id: 'indexing', label: 'Indexing' },
		...(ov?.unplaced.length ? [{ id: 'suggested', label: 'Found outside your folders' }] : [])
	]);
</script>

<div class="flex h-full min-h-0 flex-col" data-folders-pane>
	{#if ctrl.roots.loading && !ov}
		<div class="flex flex-1 items-center justify-center"><Spinner size="lg" /></div>
	{:else if !ov}
		<LoadErrorState title="Couldn't load folders" message={ctrl.roots.error ?? 'Failed to load model roots.'} retrying={ctrl.roots.loading} onRetry={() => ctrl.load()} />
	{:else}
		<div class="flex flex-shrink-0 items-center gap-2 border-b border-line bg-surface-1 px-4 py-2 md:hidden" data-mobile-switcher>
			<label class="flex min-w-0 flex-1 items-center gap-2 text-[12px] text-fg-muted">
				<span class="flex-shrink-0">Folder</span>
				<select class="input h-8 min-w-0 flex-1 py-0 text-sm" value={effective} onchange={(e) => (ctrl.selected = e.currentTarget.value)} aria-label="Folder">
					{#each selectItems as item (item.id)}<option value={item.id}>{item.label}</option>{/each}
				</select>
			</label>
			<Tooltip text="Add folder" position="bottom"><IconButton icon="folder-plus" label="Add folder" variant="secondary" onclick={() => ctrl.openAdd()} /></Tooltip>
		</div>

		{#if root}
			{#key root.id}
				<RootPane {ctrl} {root} {onBack} />
			{/key}
		{:else if effective === 'routing'}
			<RoutingPane {ctrl} {onBack} />
		{:else if effective === 'suggested'}
			<SuggestedPane {ctrl} {onBack} />
		{:else}
			<DetailHeader title="Indexing" icon="database" backLabel={$isMobile ? 'Models' : undefined} {onBack} />
			<DetailBody>
				<div class="max-w-3xl space-y-4"><IndexingSettingsSection /></div>
			</DetailBody>
		{/if}
	{/if}
</div>

<AddRootModal
	isOpen={ctrl.addOpen}
	rootsState={ctrl.roots}
	pathStyle={ov?.path_style}
	initialPath={ctrl.addPath}
	onClose={() => (ctrl.addOpen = false)}
	onCreated={(r) => {
		ctrl.handleCreated(r);
		ctrl.selected = r.id;
	}}
/>
