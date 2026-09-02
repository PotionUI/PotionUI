<script lang="ts">
	import { onMount } from 'svelte';
	import PromptWorkspace from './components/PromptWorkspace.svelte';
	import PromptsSidebar from './components/PromptsSidebar.svelte';
	import PromptImportModal from './components/PromptImportModal.svelte';
	import SavedSegmentsWorkspace from './components/SavedSegmentsWorkspace.svelte';
	import SegmentTemplatesWorkspace from './components/SegmentTemplatesWorkspace.svelte';
	import SegmentCategoriesWorkspace from './components/SegmentCategoriesWorkspace.svelte';
	import { Button, IconButton, PageContainer, PageHeader } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { api } from '$lib/services/api';
	import type { PromptImporter } from '$lib/services/api/prompts';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import { logger } from '$lib/utils/logger';
	import { toasts } from '$lib/stores/toast';

	type LibraryTab = 'prompts' | 'segments' | 'templates' | 'categories';

	let activeTab: LibraryTab = 'prompts';
	let importMenuOpen = false;
	let importMenuEl: HTMLDivElement;

	// Core ships one import source (file/text upload, in-app modal); plugins
	// can register more, listed below it in the same menu. With no plugin
	// importers registered, Import opens the core modal directly - no dropdown
	// pointing at a single item.
	let importers: PromptImporter[] = [];
	let activeImporter: PromptImporter | null = null;
	let coreImportOpen = false;
	let exporting = false;

	// PromptWorkspace owns the models list and the composer/duplicate-scan
	// state — the toolbar just triggers them so there's one copy of that
	// state, not two.
	let promptWorkspace: PromptWorkspace;

	// The collection folder tree only applies to the Prompts tab - segments,
	// templates and categories have no collections of their own.
	let sidebarOpen = true;
	let activeCollectionId: string | undefined = undefined;

	async function selectAllPrompts() {
		activeCollectionId = undefined;
		await promptWorkspace?.setCollectionFilter(undefined);
	}

	async function selectCollectionFolder(id: string) {
		activeCollectionId = id;
		await promptWorkspace?.setCollectionFilter(id);
	}

	const libraryTabs: Array<{ id: LibraryTab; label: string; icon: string }> = [
		{ id: 'prompts', label: 'Prompts', icon: 'document' },
		{ id: 'segments', label: 'Segments', icon: 'list' },
		{ id: 'templates', label: 'Segment Templates', icon: 'layout-template' },
		{ id: 'categories', label: 'Segment Categories', icon: 'folder' }
	];

	onMount(async () => {
		try {
			const response = await api.listPromptImporters();
			if (response.success && response.data) importers = response.data;
		} catch (err) {
			logger.error('Failed to load prompt importers:', err);
		}
	});

	/** "plugin:<id>:<asset>" -> {pluginId, asset}, or null if malformed. */
	function parseComponentRef(ref: string): { pluginId: string; asset: string } | null {
		const parts = ref.split(':');
		if (parts[0] !== 'plugin' || parts.length < 3) return null;
		return { pluginId: parts[1], asset: parts.slice(2).join(':') };
	}

	function openImporter(importer: PromptImporter) {
		importMenuOpen = false;
		activeImporter = importer;
	}

	$: activeImporterRef = activeImporter ? parseComponentRef(activeImporter.component) : null;

	async function closeImporter() {
		activeImporter = null;
	}

	async function handleImported() {
		activeImporter = null;
		await promptWorkspace?.reloadPrompts();
	}

	function openImportMenu() {
		if (importers.length === 0) {
			coreImportOpen = true;
			return;
		}
		importMenuOpen = !importMenuOpen;
	}

	function openCoreImport() {
		importMenuOpen = false;
		coreImportOpen = true;
	}

	function closeCoreImport() {
		coreImportOpen = false;
	}

	async function handleCoreImported() {
		await promptWorkspace?.reloadPrompts();
	}

	async function handleExport() {
		if (exporting) return;
		exporting = true;
		try {
			await api.downloadPromptsExport(activeCollectionId ? { collection_id: activeCollectionId } : {});
		} catch (err) {
			logger.error('Failed to export prompts:', err);
			toasts.error('Failed to export prompts');
		} finally {
			exporting = false;
		}
	}

	function onWindowClick(e: MouseEvent) {
		if (importMenuOpen && importMenuEl && !importMenuEl.contains(e.target as Node)) importMenuOpen = false;
	}

	function onWindowKey(e: KeyboardEvent) {
		if (importMenuOpen && e.key === 'Escape') importMenuOpen = false;
	}
</script>

<svelte:head><title>Prompt Library</title></svelte:head>
<svelte:window on:click={onWindowClick} on:keydown={onWindowKey} />

<div class="flex h-[100dvh] flex-col bg-canvas text-fg">
	<PageHeader sticky={false} wrap>
		<div class="flex min-w-0 items-center gap-3">
			<span class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-surface-2 border border-line-strong">
				<Icon name="document" className="h-4 w-4 text-fg-muted" />
			</span>
			<div class="min-w-0">
				<h1 class="truncate text-sm font-semibold text-fg">Prompt Library</h1>
				<p class="hidden text-xs text-fg-muted sm:block">Every reusable prompt, one searchable place</p>
			</div>

			<nav class="flex min-w-0 items-center gap-1 overflow-x-auto" aria-label="Prompt library sections">
			{#each libraryTabs as tab}
				<button
					type="button"
					class="flex min-w-max items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors {activeTab ===
					tab.id
						? 'bg-signal/10 text-signal'
						: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
					onclick={() => (activeTab = tab.id)}
				>
					<Icon name={tab.icon} className="h-3.5 w-3.5" />
					{tab.label}
				</button>
			{/each}
			</nav>
		</div>

		<div class="flex items-center gap-2">
			{#if activeTab === 'prompts'}
				<Tooltip text="Export as styles.csv" position="bottom">
					<IconButton icon="download" label="Export as styles.csv" onclick={handleExport} disabled={exporting} />
				</Tooltip>

				<div class="relative" bind:this={importMenuEl}>
					<button
						type="button"
						class="flex items-center gap-1.5 rounded bg-surface-3 px-3 py-1.5 text-xs font-medium text-fg transition-colors hover:bg-line-hover"
						aria-haspopup={importers.length > 0 ? 'menu' : undefined}
						aria-expanded={importers.length > 0 ? importMenuOpen : undefined}
						onclick={openImportMenu}
					>
						<Icon name="upload" className="h-3.5 w-3.5" />
						Import
						{#if importers.length > 0}
							<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
						{/if}
					</button>
					{#if importMenuOpen && importers.length > 0}
						<div
							class="absolute right-0 top-[calc(100%+6px)] z-30 min-w-[200px] overflow-hidden rounded-xl border border-line-strong bg-surface-2 shadow-floating"
							role="menu"
						>
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
								onclick={openCoreImport}
							>
								<Icon name="upload" className="h-3.5 w-3.5" />
								From file or text
							</button>
							{#each importers as importer (importer.id)}
								<button
									type="button"
									role="menuitem"
									class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
									onclick={() => openImporter(importer)}
								>
									<Icon name="upload" className="h-3.5 w-3.5" />
									{importer.label}
								</button>
							{/each}
						</div>
					{/if}
				</div>

				<Button size="sm" icon="copy" onclick={() => promptWorkspace?.openDuplicatesScan()}>
					Duplicates
				</Button>

				<Button size="sm" variant="primary" icon="plus" onclick={() => promptWorkspace?.openComposer()}>
					New prompt
				</Button>
			{/if}
		</div>
	</PageHeader>

	<div class="flex min-h-0 flex-1">
		{#if activeTab === 'prompts'}
			{#if sidebarOpen}
				<aside
					class="hidden md:block w-60 flex-shrink-0 self-stretch border-r border-line bg-surface-1 z-20"
				>
					<div class="sticky top-0 h-full overflow-hidden">
						<PromptsSidebar
							activeId={activeCollectionId}
							onSelectAll={selectAllPrompts}
							onSelectFolder={selectCollectionFolder}
							onCollapse={() => (sidebarOpen = false)}
						/>
					</div>
				</aside>
			{:else}
				<aside class="hidden md:block w-8 flex-shrink-0 self-stretch border-r border-line bg-surface-1 z-20">
					<button
						class="sticky top-0 flex h-full w-full flex-col items-center gap-2 pt-3 text-fg-subtle hover:text-fg hover:bg-surface-2 transition-colors"
						onclick={() => (sidebarOpen = true)}
						title="Show collections"
						aria-label="Show collections"
					>
						<Icon name="chevron-right" className="w-4 h-4" />
						<Icon name="folder" className="w-4 h-4" />
					</button>
				</aside>
			{/if}
		{/if}

		<PageContainer width="full" class="min-h-0 flex-1 !max-w-none !px-0 !py-0">
			<div class:hidden={activeTab !== 'prompts'} class="h-full">
				<PromptWorkspace bind:this={promptWorkspace} />
			</div>
			{#if activeTab === 'segments'}<SavedSegmentsWorkspace />
			{:else if activeTab === 'templates'}<SegmentTemplatesWorkspace />
			{:else if activeTab === 'categories'}<SegmentCategoriesWorkspace />{/if}
		</PageContainer>
	</div>
</div>

{#if activeImporterRef}
	{#await resolvePluginComponent(activeImporterRef.pluginId, activeImporterRef.asset) then Component}
		{#if Component}
			<svelte:component this={Component} onClose={closeImporter} onImported={handleImported} />
		{/if}
	{/await}
{/if}

{#if coreImportOpen}
	<PromptImportModal onClose={closeCoreImport} onImported={handleCoreImported} />
{/if}
