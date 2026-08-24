<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { MasterDetailLayout, DetailPane } from '$lib/components/master-detail';
	import { Pane, PaneRow } from '$lib/components/pane';
	import { Badge, Button, Card, Input } from '$lib/components/ui';
	import type { Segment, SegmentTemplate } from '$lib/types/segments';
	import { createBlankEditorSegment, toEditorSegment, toRichSegment } from '$lib/utils/richSegments';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';

	let templates: SegmentTemplate[] = [];
	let selected: SegmentTemplate | null = null;
	let name = '';
	let description = '';
	let tagsText = '';
	let query = '';
	let editorSegments: Segment[] = [createBlankEditorSegment()];
	let loading = false;
	let saving = false;

	$: normalizedQuery = query.trim().toLowerCase();
	$: filteredTemplates = normalizedQuery
		? templates.filter((template) =>
				[template.name, template.description || '', ...(template.tags || [])]
					.join(' ')
					.toLowerCase()
					.includes(normalizedQuery)
			)
		: templates;

	onMount(load);

	async function load() {
		loading = true;
		try {
			templates = (await api.listSegmentTemplates()).data?.templates || [];
		} catch {
			toasts.error('Failed to load Segment Templates');
		} finally {
			loading = false;
		}
	}

	function selectTemplate(template: SegmentTemplate) {
		selected = template;
		name = template.name;
		description = template.description || '';
		tagsText = (template.tags || []).join(', ');
		editorSegments = template.segments.map((segment) => toEditorSegment(segment));
		if (!editorSegments.length) editorSegments = [createBlankEditorSegment()];
	}

	function createNew() {
		selected = null;
		name = '';
		description = '';
		tagsText = '';
		editorSegments = [createBlankEditorSegment()];
	}

	function resetTemplate() {
		if (selected) selectTemplate(selected);
		else createNew();
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
			tags: tagsText
				.split(',')
				.map((tag) => tag.trim())
				.filter(Boolean),
			segments: editorSegments.map(toRichSegment)
		};
		try {
			const response = selected
				? await api.updateSegmentTemplate(selected.id, payload)
				: await api.createSegmentTemplate(payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success(selected ? 'Template updated' : 'Template saved');
			await load();
			selectTemplate(response.data);
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save Template');
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (!selected) return;
		if (
			!(await confirmDialog({
				title: 'Delete',
				message: `Delete “${selected.name}”?`,
				variant: 'danger'
			}))
		)
			return;
		try {
			await api.deleteSegmentTemplate(selected.id);
			toasts.success('Template deleted');
			createNew();
			await load();
		} catch {
			toasts.error('Failed to delete Segment Template');
		}
	}
</script>

<div class="h-full">
	<MasterDetailLayout
		leftWidth={340}
		minWidth={260}
		maxWidth={480}
		storageKey="segment-templates-panel-width"
	>
		<svelte:fragment slot="list">
			<Pane
				label="Segment Templates"
				count={filteredTemplates.length}
				searchable
				bind:search={query}
				searchPlaceholder="Search Segment Templates..."
				{loading}
				isEmpty={filteredTemplates.length === 0}
			>
				{#snippet headerActions()}
					<Button variant="primary" size="xs" icon="plus" onclick={createNew}>New Template</Button>
				{/snippet}

				{#snippet empty()}
					<div class="px-4 py-10 text-center">
						<Icon
							name="layout-template"
							className="mx-auto mb-3 h-9 w-9 text-fg-disabled"
							strokeWidth={1.5}
						/>
						<p class="text-sm text-fg-muted">
							{query.trim() ? 'No Segment Templates match your search' : 'No Segment Templates yet'}
						</p>
					</div>
				{/snippet}

				{#snippet children()}
					{#each filteredTemplates as template (template.id)}
						{#snippet meta()}
							<div class="mt-2 flex flex-wrap items-center gap-1.5">
								<Badge size="sm" variant="signal">
									{template.segments.length} slot{template.segments.length === 1 ? '' : 's'}
								</Badge>
								{#each (template.tags || []).slice(0, 2) as tag}
									<Badge size="sm">{tag}</Badge>
								{/each}
							</div>
						{/snippet}
						<PaneRow
							title={template.name}
							subtitle={template.description || 'Ordered rich slot layout'}
							selected={selected?.id === template.id}
							onclick={() => selectTemplate(template)}
							{meta}
						/>
					{/each}
				{/snippet}
			</Pane>
		</svelte:fragment>

		<svelte:fragment slot="detail">
			<DetailPane
				title={selected ? 'Edit Segment Template' : 'New Segment Template'}
				showDelete={Boolean(selected)}
				showCancel={Boolean(selected)}
				saveLabel={selected ? 'Save changes' : 'Save Template'}
				saveDisabled={!name.trim()}
				isLoading={saving}
				on:save={save}
				on:cancel={resetTemplate}
				on:delete={remove}
			>
				<div class="space-y-4">
					<Card padding="sm">
						<h3 class="label mb-3">Template details</h3>
						<div class="grid gap-3 sm:grid-cols-2">
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">
									Name <span class="text-danger">*</span>
								</span>
								<Input class="text-sm" bind:value={name} placeholder="Template name" />
							</label>
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">
									Tags <span class="font-normal text-fg-subtle">(comma separated)</span>
								</span>
								<Input class="text-sm" bind:value={tagsText} placeholder="portrait, lighting" />
							</label>
						</div>
						<label class="mt-3 block">
							<span class="mb-1.5 block text-xs font-medium text-fg-muted">Description</span>
							<textarea
								class="input resize-y text-sm"
								rows="2"
								bind:value={description}
								placeholder="What this layout is for"
							></textarea>
						</label>
					</Card>

					<SegmentedPromptEditor
						segments={editorSegments}
						label="Template slots"
						compact
						showLibraryActions={false}
						on:segmentsChange={(event) => (editorSegments = event.detail)}
					/>
				</div>
			</DetailPane>
		</svelte:fragment>
	</MasterDetailLayout>
</div>
