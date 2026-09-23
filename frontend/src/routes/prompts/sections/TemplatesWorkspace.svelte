<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/services/api';
	import { Button, EmptyState, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import { libraryCounts, setLibraryCount } from '../library/libraryCounts';
	import { LIBRARY_SECTIONS, sectionHref, withSection } from '../library/librarySection';
	import TemplateCard from './TemplateCard.svelte';
	import TemplateDetailView from './TemplateDetailView.svelte';
	import TemplateFiltersPopover from './TemplateFiltersPopover.svelte';
	import {
		TEMPLATE_SORT_OPTIONS,
		applyTemplateFilters,
		clearAllTemplateFilters,
		clearTemplateFilterChip,
		templateFilterActiveCount,
		templateFilterChips,
		templateFiltersFromSearchParams,
		templateFiltersToSearchParams,
		templateTagVocabulary,
		type TemplateFilters,
		type TemplateSortBy
	} from './templateFilters';
	import type { Segment, SegmentTemplate } from '$lib/types/segments';
	import {
		applyTemplateSegments,
		createBlankEditorSegment,
		toEditorSegment,
		toRichSegment
	} from '$lib/utils/richSegments';
	import { tabsStore, activeTab } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';

	interface FormSnapshot {
		name: string;
		description: string;
		tags: string;
		segments: string;
	}

	let templates = $state<SegmentTemplate[]>([]);
	let loading = $state(true);
	let saving = $state(false);
	let selectedIds = $state(new Set<string>());
	let gridEl: HTMLDivElement | undefined = $state();

	let lastRouteKey = $state('');
	let name = $state('');
	let description = $state('');
	let tagsText = $state('');
	let editorSegments = $state<Segment[]>([createBlankEditorSegment()]);
	let baseline = $state<FormSnapshot>({ name: '', description: '', tags: '', segments: '' });

	const filters = $derived(templateFiltersFromSearchParams($page.url.searchParams));
	const viewId = $derived($page.url.searchParams.get('id'));
	const viewIsNew = $derived($page.url.searchParams.get('new') === '1');
	const detailOpen = $derived(viewIsNew || !!viewId);
	const activeTemplate = $derived(viewId ? (templates.find((entry) => entry.id === viewId) ?? null) : null);
	const mode = $derived<'create' | 'edit'>(viewIsNew || !activeTemplate ? 'create' : 'edit');
	const visible = $derived(applyTemplateFilters(templates, filters));
	const tagVocabulary = $derived(templateTagVocabulary(templates));
	const chips = $derived(templateFilterChips(filters));
	const activeFilterCount = $derived(templateFilterActiveCount(filters));
	const narrowed = $derived(activeFilterCount > 0 || filters.q.trim().length > 0);

	const currentSnapshot = $derived(snapshotOf(name, description, tagsText, editorSegments));
	const dirtyCount = $derived(
		(currentSnapshot.name !== baseline.name ? 1 : 0) +
			(currentSnapshot.description !== baseline.description ? 1 : 0) +
			(currentSnapshot.tags !== baseline.tags ? 1 : 0) +
			(currentSnapshot.segments !== baseline.segments ? 1 : 0)
	);

	function parseTags(text: string): string[] {
		return text
			.split(',')
			.map((tag) => tag.trim())
			.filter(Boolean);
	}

	function snapshotOf(
		nameValue: string,
		descriptionValue: string,
		tagsValue: string,
		segments: Segment[]
	): FormSnapshot {
		return {
			name: nameValue.trim(),
			description: descriptionValue.trim(),
			tags: parseTags(tagsValue).join(','),
			segments: JSON.stringify(segments.map(toRichSegment))
		};
	}

	function buildUrl(
		overrides: { id?: string | null; isNew?: boolean; filters?: TemplateFilters } = {}
	): string {
		const params = withSection(templateFiltersToSearchParams(overrides.filters ?? filters), 'templates');
		const id = overrides.id !== undefined ? overrides.id : viewId;
		const isNew = overrides.isNew !== undefined ? overrides.isNew : viewIsNew;
		if (id) params.set('id', id);
		if (isNew) params.set('new', '1');
		const query = params.toString();
		return query ? `${$page.url.pathname}?${query}` : $page.url.pathname;
	}

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;

	function updateFilters(next: TemplateFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ filters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	onMount(load);

	async function load() {
		loading = true;
		try {
			const response = await api.listSegmentTemplates();
			templates = response.data?.templates ?? [];
			setLibraryCount('templates', templates.length);
		} catch {
			toasts.error('Failed to load templates');
		} finally {
			loading = false;
		}
	}

	function fillFrom(template: SegmentTemplate) {
		name = template.name;
		description = template.description || '';
		tagsText = (template.tags || []).join(', ');
		editorSegments = template.segments.length
			? template.segments.map((segment) => toEditorSegment(segment))
			: [createBlankEditorSegment()];
		baseline = snapshotOf(name, description, tagsText, editorSegments);
	}

	function enterEdit(id: string) {
		const target = templates.find((entry) => entry.id === id) ?? null;
		if (!target) {
			toasts.error('Template not found');
			backToGrid();
			return;
		}
		fillFrom(target);
	}

	function enterCreate() {
		name = '';
		description = '';
		tagsText = '';
		editorSegments = [createBlankEditorSegment()];
		baseline = snapshotOf(name, description, tagsText, editorSegments);
	}

	$effect(() => {
		if (loading) return;
		const key = viewId ? `edit:${viewId}` : viewIsNew ? 'new' : 'grid';
		if (key === untrack(() => lastRouteKey)) return;
		lastRouteKey = key;
		untrack(() => {
			if (viewId) enterEdit(viewId);
			else if (viewIsNew) enterCreate();
		});
	});

	function openTemplate(template: SegmentTemplate) {
		void goto(buildUrl({ id: template.id, isNew: false }));
	}

	function backToGrid() {
		void goto(buildUrl({ id: null, isNew: false }));
	}

	function newTemplate() {
		void goto(buildUrl({ id: null, isNew: true }));
	}

	function toggleSelect(template: SegmentTemplate) {
		const next = new Set(selectedIds);
		if (next.has(template.id)) next.delete(template.id);
		else next.add(template.id);
		selectedIds = next;
	}

	function selectAll() {
		selectedIds = new Set(visible.map((template) => template.id));
	}

	function clearSelection() {
		selectedIds = new Set();
	}

	function applyToActiveTab(payload: Pick<SegmentTemplate, 'id' | 'name' | 'segments'>) {
		const tab = get(activeTab);
		if (!tab) {
			toasts.error('Open a Generate tab first');
			return;
		}
		tabsStore.updateTab(tab.id, {
			promptSegments: applyTemplateSegments(tab.promptSegments ?? [], payload, 'append')
		});
		toasts.success('Template applied to the active Generate tab');
		void goto('/');
	}

	function applyFromCard(template: SegmentTemplate) {
		applyToActiveTab(template);
	}

	function applyFromDetail() {
		applyToActiveTab({
			id: activeTemplate?.id ?? 'draft-template',
			name: name.trim() || 'Template',
			segments: editorSegments.map(toRichSegment)
		});
	}

	async function save() {
		if (!name.trim()) {
			toasts.error('A template name is required');
			return;
		}
		saving = true;
		const payload = {
			name: name.trim(),
			description: description.trim() || null,
			tags: parseTags(tagsText),
			segments: editorSegments.map(toRichSegment)
		};
		try {
			const response =
				mode === 'edit' && activeTemplate
					? await api.updateSegmentTemplate(activeTemplate.id, payload)
					: await api.createSegmentTemplate(payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success(mode === 'edit' ? 'Template updated' : 'Template created');
			const savedId = response.data.id;
			await load();
			lastRouteKey = `edit:${savedId}`;
			enterEdit(savedId);
			await goto(buildUrl({ id: savedId, isNew: false }), { replaceState: true, noScroll: true });
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save template');
		} finally {
			saving = false;
		}
	}

	function discard() {
		if (activeTemplate) fillFrom(activeTemplate);
		else enterCreate();
	}

	async function duplicate(template: SegmentTemplate, openAfter: boolean) {
		try {
			const response = await api.createSegmentTemplate({
				name: `${template.name} copy`,
				description: template.description ?? null,
				tags: [...(template.tags ?? [])],
				segments: template.segments.map((segment) => ({ ...segment }))
			});
			if (!response.success || !response.data) throw new Error(response.error || 'Duplicate failed');
			toasts.success('Template duplicated');
			const newId = response.data.id;
			await load();
			if (openAfter) {
				lastRouteKey = `edit:${newId}`;
				enterEdit(newId);
				await goto(buildUrl({ id: newId, isNew: false }));
			}
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to duplicate template');
		}
	}

	async function removeTemplate(template: SegmentTemplate, backAfter: boolean) {
		const confirmed = await confirmDialog({
			title: `Delete “${template.name}”?`,
			message: "This can't be undone.",
			variant: 'danger'
		});
		if (!confirmed) return;
		try {
			const response = await api.deleteSegmentTemplate(template.id);
			if (!response.success) throw new Error(response.error || 'Delete failed');
			toasts.success('Template deleted');
			await load();
			if (backAfter) {
				lastRouteKey = 'grid';
				backToGrid();
			}
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to delete template');
		}
	}

	async function bulkDelete() {
		const targets = templates.filter((template) => selectedIds.has(template.id));
		if (!targets.length) return;
		const confirmed = await confirmDialog({
			title: `Delete ${targets.length} template${targets.length === 1 ? '' : 's'}?`,
			message: "This can't be undone.",
			variant: 'danger'
		});
		if (!confirmed) return;
		const removed: string[] = [];
		for (const template of targets) {
			try {
				const response = await api.deleteSegmentTemplate(template.id);
				if (response.success) removed.push(template.id);
			} catch {
				continue;
			}
		}
		if (removed.length) toasts.success(`Deleted ${removed.length} template${removed.length === 1 ? '' : 's'}`);
		const failed = targets.length - removed.length;
		if (failed > 0) toasts.error(`Failed to delete ${failed} template${failed === 1 ? '' : 's'}`);
		clearSelection();
		await load();
	}

	function handleGridKeydown(event: KeyboardEvent) {
		if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
		const cards = Array.from(gridEl?.querySelectorAll<HTMLElement>('[data-template-card]') ?? []);
		const currentIndex = cards.indexOf(document.activeElement as HTMLElement);
		if (currentIndex === -1) return;
		event.preventDefault();
		const forward = event.key === 'ArrowRight' || event.key === 'ArrowDown';
		const nextIndex = forward ? Math.min(cards.length - 1, currentIndex + 1) : Math.max(0, currentIndex - 1);
		cards[nextIndex]?.focus();
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape') return;
		if (selectedIds.size > 0) clearSelection();
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<LibraryShell
	title="Prompt Library"
	persistKey="prompt-library"
	sections={LIBRARY_SECTIONS}
	section="templates"
	onSelectSection={(id) => goto(sectionHref(id))}
	sectionCounts={$libraryCounts}
	count={visible.length}
	{detailOpen}
	filterChips={chips}
	onRemoveChip={(key) => updateFilters(clearTemplateFilterChip(filters, key))}
	onClearFilters={() => updateFilters(clearAllTemplateFilters(filters))}
	loadedCount={visible.length}
	total={templates.length}
>
	{#snippet toolbar()}
		<LibraryFilterBar
			q={filters.q}
			onQueryChange={(value) => updateFilters({ ...filters, q: value })}
			searchPlaceholder="Search templates…"
			sortBy={filters.sortBy}
			sortOptions={TEMPLATE_SORT_OPTIONS}
			onSortChange={(value) => updateFilters({ ...filters, sortBy: value as TemplateSortBy })}
			filterCount={activeFilterCount}
		>
			{#snippet popover(close: () => void)}
				<TemplateFiltersPopover {filters} tags={tagVocabulary} onChange={updateFilters} onClose={close} />
			{/snippet}
		</LibraryFilterBar>
	{/snippet}

	{#snippet primary()}
		<Button size="sm" variant="primary" icon="plus" onclick={newTemplate}>New template</Button>
	{/snippet}

	{#if detailOpen}
		{#if viewId && !activeTemplate}
			<div class="flex h-full items-center justify-center">
				<Spinner size="lg" />
			</div>
		{:else}
			<TemplateDetailView
				{mode}
				template={activeTemplate}
				bind:name
				bind:description
				bind:tagsText
				bind:editorSegments
				{saving}
				{dirtyCount}
				onBack={backToGrid}
				onSave={save}
				onDiscard={discard}
				onDelete={() => activeTemplate && void removeTemplate(activeTemplate, true)}
				onDuplicate={() => activeTemplate && void duplicate(activeTemplate, true)}
				onApply={applyFromDetail}
			/>
		{/if}
	{:else}
		<div class="h-full overflow-y-auto p-4">
			{#if loading}
				<div class="flex h-40 items-center justify-center">
					<Spinner size="lg" />
				</div>
			{:else if visible.length === 0}
				<div class="flex h-full items-center justify-center">
					<EmptyState
						icon="layout-template"
						title={narrowed ? 'No templates match these filters' : 'No templates yet'}
						description={narrowed
							? 'Try clearing a filter or broadening the search.'
							: 'A template is a reusable slot layout you can drop into any prompt.'}
					>
						{#snippet actions()}
							{#if narrowed}
								<Button size="sm" variant="ghost" onclick={() => updateFilters(clearAllTemplateFilters(filters))}>
									Clear filters
								</Button>
							{:else}
								<Button size="sm" variant="primary" icon="plus" onclick={newTemplate}>Create a template</Button>
							{/if}
						{/snippet}
					</EmptyState>
				</div>
			{:else}
				<div
					bind:this={gridEl}
					class="columns-[360px] gap-3"
					role="toolbar"
					aria-label="Template cards"
					aria-orientation="horizontal"
					tabindex="-1"
					onkeydown={handleGridKeydown}
				>
					{#each visible as template (template.id)}
						<TemplateCard
							{template}
							selected={selectedIds.has(template.id)}
							onToggleSelect={toggleSelect}
							onOpen={openTemplate}
							onApply={applyFromCard}
							onDuplicate={(entry) => void duplicate(entry, false)}
							onDelete={(entry) => void removeTemplate(entry, false)}
						/>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</LibraryShell>

<SelectionActionBar
	active={selectedIds.size > 0}
	selectedCount={selectedIds.size}
	totalCount={visible.length}
	onSelectAll={selectAll}
	onClearSelection={clearSelection}
	onClose={clearSelection}
	feedback={null}
>
	<svelte:fragment slot="actionsAfterCollection">
		<Tooltip text="Delete the selected templates">
			<button
				class="flex items-center gap-2 rounded bg-danger-solid px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-danger-solid/90"
				onclick={bulkDelete}
			>
				<Icon name="trash" className="h-4 w-4" />
				Delete
			</button>
		</Tooltip>
	</svelte:fragment>
</SelectionActionBar>
