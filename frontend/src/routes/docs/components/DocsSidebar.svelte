<script lang="ts">
	import { iconPaths } from '$lib/utils/IconLibrary';
	import { Input, Spinner } from '$lib/components/ui';
	import { buildDocNavigation } from '$lib/stores/docs';
	import { showsStatusDot, statusBadgeVariant } from '$lib/utils/docsMeta';
	import type { DocItem, DocSection } from '$lib/types/api';

	export let sections: DocSection[] = [];
	export let loading = false;
	export let error: string | null = null;
	export let selectedId: string | null = null;
	export let onSelect: (id: string) => void;

	let filterText = '';

	// Section/category collapse state defaults to expanded and stays stable as
	// filter results change.
	let collapsedSections: Record<string, boolean> = {};
	let collapsedCategories: Record<string, boolean> = {};

	$: filteredSections = buildDocNavigation(sections, filterText);

	// While filtering, force every matching ancestor open so results are visible.
	$: isFiltering = filterText.trim().length > 0;

	function toggleSection(id: string) {
		collapsedSections = { ...collapsedSections, [id]: !collapsedSections[id] };
	}

	function categoryKey(sectionId: string, categoryId: string): string {
		return `${sectionId}:${categoryId}`;
	}

	function toggleCategory(sectionId: string, categoryId: string) {
		const key = categoryKey(sectionId, categoryId);
		collapsedCategories = { ...collapsedCategories, [key]: !collapsedCategories[key] };
	}

	function sectionIsCollapsed(id: string): boolean {
		return !isFiltering && Boolean(collapsedSections[id]);
	}

	function categoryIsCollapsed(sectionId: string, categoryId: string): boolean {
		return !isFiltering && Boolean(collapsedCategories[categoryKey(sectionId, categoryId)]);
	}

	function iconPath(name: string): string {
		const p = iconPaths[name];
		return Array.isArray(p) ? p[0] : (p ?? '');
	}

	// technique/model are Docs 2.0 typed frontmatter kinds (`doc_type`, #48/#50)
	// -- `type` itself stays 'markdown'/'live' for every doc. Untyped markdown
	// keeps its pre-existing icon.
	function itemIcon(item: DocItem): string {
		if (item.type === 'live') return 'code';
		switch (item.doc_type) {
			case 'technique':
				return 'sparkles';
			case 'model':
				return 'model';
			default:
				return 'document';
		}
	}

	const statusDotClasses: Record<'success' | 'warning' | 'info' | 'neutral', string> = {
		success: 'bg-success',
		warning: 'bg-warning',
		info: 'bg-info',
		neutral: 'bg-fg-subtle'
	};

	function statusDotClass(item: DocItem): string {
		return statusDotClasses[statusBadgeVariant(item.status)];
	}
</script>

<div class="w-full md:w-72 max-h-64 md:max-h-none flex-shrink-0 border-b md:border-b-0 md:border-r border-line flex flex-col bg-surface-1">
	<div class="p-3 border-b border-line">
		<Input
			type="search"
			bind:value={filterText}
			placeholder="Filter documentation..."
			aria-label="Filter documentation"
		/>
	</div>

	<div class="flex-1 overflow-y-auto py-2">
		{#if loading && sections.length === 0}
			<div class="flex items-center justify-center py-8">
				<Spinner size="md" />
			</div>
		{:else if error}
			<div class="px-4 py-6 text-sm text-danger">{error}</div>
		{:else if sections.length === 0}
			<div class="px-4 py-6 text-sm text-fg-subtle">No documentation available.</div>
		{:else if filteredSections.length === 0}
			<div class="px-4 py-6 text-sm text-fg-subtle">No matches for "{filterText}".</div>
		{:else}
			{#each filteredSections as section (section.id)}
				<div class="mb-1">
					<button
						type="button"
						class="w-full flex items-center gap-2 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-fg-muted hover:text-fg transition-colors disabled:cursor-default"
						on:click={() => toggleSection(section.id)}
						aria-expanded={!sectionIsCollapsed(section.id)}
						disabled={isFiltering}
					>
						<svg
							class="w-3 h-3 flex-shrink-0 transition-transform {sectionIsCollapsed(section.id) ? '-rotate-90' : ''}"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
						</svg>
						<span class="truncate">{section.title}</span>
					</button>

					{#if !sectionIsCollapsed(section.id)}
						<div class="space-y-0.5 px-2">
							{#each section.entries as entry (`${entry.kind}:${entry.id}`)}
								{#if entry.kind === 'category'}
									<button
										type="button"
										class="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm font-medium text-left text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:cursor-default"
										on:click={() => toggleCategory(section.id, entry.id)}
										aria-expanded={!categoryIsCollapsed(section.id, entry.id)}
										disabled={isFiltering}
									>
										<svg
											class="w-3 h-3 flex-shrink-0 transition-transform {categoryIsCollapsed(section.id, entry.id) ? '-rotate-90' : ''}"
											fill="none"
											stroke="currentColor"
											viewBox="0 0 24 24"
										>
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
										</svg>
										<span class="truncate">{entry.title}</span>
										<span class="ml-auto text-xs font-normal tabular-nums text-fg-subtle">
											{entry.items.length}
										</span>
									</button>

									{#if !categoryIsCollapsed(section.id, entry.id)}
										<div class="ml-3 pl-2 border-l border-line space-y-0.5">
											{#each entry.items as item (item.id)}
												<button
													type="button"
													class="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm text-left transition-colors
														{selectedId === item.id
															? 'bg-signal/10 text-signal'
															: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
													on:click={() => onSelect(item.id)}
													aria-current={selectedId === item.id ? 'page' : undefined}
												>
													<svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
														<path
															stroke-linecap="round"
															stroke-linejoin="round"
															stroke-width="2"
															d={iconPath(itemIcon(item))}
														/>
													</svg>
													<span class="truncate">{item.title}</span>
													<span class="ml-auto flex-shrink-0 flex items-center gap-1">
														{#if showsStatusDot(item.status)}
															<span
																class="w-1.5 h-1.5 rounded-full {statusDotClass(item)}"
																title="Status: {item.status}"
															></span>
														{/if}
														{#if item.source === 'plugin'}
															<span
																class="w-1.5 h-1.5 rounded-full bg-fg-subtle"
																title="Provided by a plugin"
															></span>
														{/if}
													</span>
												</button>
											{/each}
										</div>
									{/if}
								{:else}
									<button
										type="button"
										class="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm text-left transition-colors
											{selectedId === entry.item.id
												? 'bg-signal/10 text-signal'
												: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
										on:click={() => onSelect(entry.item.id)}
										aria-current={selectedId === entry.item.id ? 'page' : undefined}
									>
										<svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												stroke-width="2"
												d={iconPath(itemIcon(entry.item))}
											/>
										</svg>
										<span class="truncate">{entry.item.title}</span>
										<span class="ml-auto flex-shrink-0 flex items-center gap-1">
											{#if showsStatusDot(entry.item.status)}
												<span
													class="w-1.5 h-1.5 rounded-full {statusDotClass(entry.item)}"
													title="Status: {entry.item.status}"
												></span>
											{/if}
											{#if entry.item.source === 'plugin'}
												<span
													class="w-1.5 h-1.5 rounded-full bg-fg-subtle"
													title="Provided by a plugin"
												></span>
											{/if}
										</span>
									</button>
								{/if}
							{/each}
						</div>
					{/if}
				</div>
			{/each}
		{/if}
	</div>
</div>
