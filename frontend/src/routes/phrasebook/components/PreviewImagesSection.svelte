<script lang="ts">
	import { previewGenerationStore } from '$lib/stores/previewGeneration';
	import {
		phrasebookStore,
		selectedCategoryValues,
		selectedCount,
		valuesWithoutPreviewIds,
		previewCount
	} from '$lib/stores/phrasebook';
	import { api } from '$lib/services/api/index';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import { Button, Alert, Badge, Switch } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { Segment } from '$lib/types/segments';

	// Owns the whole "Preview images" tab of the category detail panel: the
	// selection-status header row, the generation form (Target/Prompt/Advanced),
	// the queue/generate action row, and the strip of values that already have
	// a preview. Everything below the header row is disabled/dimmed until at
	// least one value is selected in the Values pane.
	let { categoryId }: { categoryId: string } = $props();

	let gen = $derived($previewGenerationStore);
	let values = $derived($selectedCategoryValues);
	let missingIds = $derived($valuesWithoutPreviewIds);
	let withPreview = $derived($previewCount);
	let selectedPresetName = $derived(gen.presets.find((p) => p.id === gen.selectedPresetId)?.name ?? '');
	let selectedModeLabel = $derived(gen.modes.find((m) => m.name === gen.selectedMode)?.label ?? gen.selectedMode);
	let valuesWithPreview = $derived(values.filter((v) => v.preview_file_id));

	let advancedOpen = $state(false);
	let existingPreviewsOpen = $state(false);

	let advancedSummary = $derived(
		[gen.useFixedSeed ? `seed ${gen.fixedSeed}` : null, gen.negativePrompt.trim() ? 'negative set' : null]
			.filter(Boolean)
			.join(' · ')
	);

	function handleGeneratePreviews() {
		previewGenerationStore.handleGeneratePreviews(categoryId, $phrasebookStore.selectedValueIds);
	}

	function handlePromptSegmentsChange(event: CustomEvent<Segment[]>) {
		previewGenerationStore.setPromptSegments(event.detail);
	}
</script>

<div class="flex flex-col gap-4">
	<div class="rounded-lg border border-line bg-surface-1 px-4 py-2.5 flex items-center gap-3 flex-wrap">
		<p class="text-xs text-fg-muted leading-relaxed">
			One generation per value selected in the Values pane &mdash;
			<span class="font-mono tabular-nums text-fg">{$selectedCount} of {values.length}</span> selected.
		</p>
		<div class="flex items-center gap-1 flex-shrink-0 ml-auto">
			<Button variant="ghost" size="xs" onclick={() => phrasebookStore.selectAllValues()}>Select all</Button>
			{#if missingIds.length > 0}
				<Button variant="ghost" size="xs" onclick={() => phrasebookStore.selectValuesWithoutPreview()}>
					Select missing ({missingIds.length})
				</Button>
			{/if}
		</div>
		<Badge size="sm" class="font-mono flex-shrink-0">{withPreview} / {values.length} HAVE A PREVIEW</Badge>
	</div>

	<div class="flex flex-col gap-4 {$selectedCount === 0 ? 'opacity-45 pointer-events-none' : ''}">
		<DetailSection label="Target">
			<div class="grid grid-cols-3 gap-4">
				<div>
					<label for="preview-preset" class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">Preset</label>
					<select
						id="preview-preset"
						class="input text-sm"
						value={gen.selectedPresetId}
						onchange={(e) => {
							previewGenerationStore.setSelectedPresetId(e.currentTarget.value);
							previewGenerationStore.handlePresetChange();
						}}
					>
						{#if gen.presets.length === 0}
							<option value="">No presets available</option>
						{:else}
							{#each gen.presets as preset}
								<option value={preset.id}>{preset.name}</option>
							{/each}
						{/if}
					</select>
				</div>
				<div>
					<label for="preview-session" class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">Session</label>
					<select
						id="preview-session"
						class="input text-sm"
						value={gen.selectedSessionId}
						onchange={(e) => previewGenerationStore.setSelectedSessionId(e.currentTarget.value)}
						disabled={!gen.selectedPresetId}
					>
						{#if gen.sessions.length === 0}
							<option value="">No sessions for this preset</option>
						{:else}
							{#each gen.sessions as session}
								<option value={session.id}>{session.name}</option>
							{/each}
						{/if}
					</select>
				</div>
				<div>
					<label for="preview-mode" class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">Mode</label>
					<select
						id="preview-mode"
						class="input text-sm"
						value={gen.selectedMode}
						onchange={(e) => previewGenerationStore.setSelectedMode(e.currentTarget.value)}
						disabled={!gen.selectedPresetId || gen.modes.length === 0}
					>
						{#if gen.modes.length === 0}
							<option value="">No modes available</option>
						{:else}
							{#each gen.modes as mode}
								<option value={mode.name}>{mode.label}</option>
							{/each}
						{/if}
					</select>
				</div>
			</div>
			{#if gen.selectedPresetId && gen.sessions.length === 0}
				<Alert variant="warning" density="compact" class="mt-3">
					No sessions for this preset &mdash; save one on the Generation page first. Sessions carry the model, steps, CFG and size.
				</Alert>
			{/if}
		</DetailSection>

		<DetailSection label="Prompt">
			{#snippet headerExtra()}
				<Tooltip text={'Placeholder for each value’s text — e.g. "A photo of << value >>" becomes "A photo of sunset".'}>
					<code class="text-2xs px-1.5 py-0.5 bg-signal/10 text-signal rounded font-mono cursor-help">{'<< value >>'}</code>
				</Tooltip>
			{/snippet}
			<SegmentedPromptEditor
				segments={gen.promptSegments}
				label="Prompt template"
				compact
				showLibraryActions={false}
				on:segmentsChange={handlePromptSegmentsChange}
			/>
		</DetailSection>

		<DetailSection label="Advanced" collapsible bind:open={advancedOpen}>
			{#snippet headerExtra()}
				{#if !advancedOpen && advancedSummary}
					<span class="font-mono text-2xs text-fg-subtle">{advancedSummary}</span>
				{/if}
			{/snippet}
			<div class="flex flex-col gap-3.5">
				<div>
					<label for="preview-negative" class="block text-xs text-fg-muted mb-1.5">Negative prompt</label>
					<textarea
						id="preview-negative"
						class="input font-mono text-sm"
						rows="2"
						placeholder="Leave empty to use session's negative prompt"
						value={gen.negativePrompt}
						oninput={(e) => previewGenerationStore.setNegativePrompt(e.currentTarget.value)}
					></textarea>
				</div>
				<div>
					<div class="flex items-center gap-2">
						<span class="text-sm text-fg-muted">Use fixed seed</span>
						<Switch
							checked={gen.useFixedSeed}
							onchange={(checked) => previewGenerationStore.setUseFixedSeed(checked)}
							label="Use fixed seed"
							size="sm"
						/>
					</div>
					{#if gen.useFixedSeed}
						<input
							type="number"
							class="input text-sm mt-2 tabular-nums"
							placeholder="Seed value"
							value={gen.fixedSeed}
							oninput={(e) => previewGenerationStore.setFixedSeed(parseInt(e.currentTarget.value, 10))}
						/>
					{/if}
					<p class="text-xs text-fg-subtle mt-1.5">
						{gen.useFixedSeed ? 'All previews will use the same seed' : 'Each preview gets a random seed'}
					</p>
				</div>
			</div>
		</DetailSection>

		<div class="rounded-lg border border-line bg-surface-1 shadow-raised px-4 sm:px-5 py-2.5 flex items-center justify-between gap-3">
			<div class="min-w-0 flex-1">
				{#if gen.isGeneratingPreviews}
					{#if gen.previewBatchProgress}
						<div class="h-1 rounded bg-surface-3 overflow-hidden mb-1.5">
							<div
								class="h-full bg-signal-solid rounded"
								style="width: {gen.previewBatchProgress.total > 0 ? (gen.previewBatchProgress.done / gen.previewBatchProgress.total) * 100 : 0}%"
							></div>
						</div>
					{/if}
					<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted truncate block">
						{gen.previewGenerationStatus ?? 'Generating…'}
					</span>
				{:else}
					<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted truncate block">
						<span class="tabular-nums">{$selectedCount}</span> GENERATION{$selectedCount !== 1 ? 'S' : ''}
						{#if selectedPresetName} &middot; {selectedPresetName}{/if}
						{#if selectedModeLabel} &middot; {selectedModeLabel}{/if}
					</span>
				{/if}
			</div>
			<Button
				variant="primary"
				icon="sparkles"
				class="flex-shrink-0"
				loading={gen.isGeneratingPreviews}
				disabled={!gen.selectedSessionId || !gen.selectedMode || gen.sessions.length === 0 || $selectedCount === 0}
				onclick={handleGeneratePreviews}
			>
				{#if gen.isGeneratingPreviews}
					Generating&hellip;
				{:else}
					Generate <span class="tabular-nums">{$selectedCount}</span> preview{$selectedCount !== 1 ? 's' : ''}
				{/if}
			</Button>
		</div>

		{#if !gen.isGeneratingPreviews && gen.previewGenerationStatus}
			<Alert variant={gen.previewGenerationStatus.includes('Error') ? 'danger' : 'signal'} density="compact">
				{gen.previewGenerationStatus}
			</Alert>
		{/if}
	</div>

	{#if withPreview > 0}
		<DetailSection label="Existing previews ({withPreview})" collapsible bind:open={existingPreviewsOpen}>
			<div class="flex gap-2.5 overflow-x-auto pb-1">
				{#each valuesWithPreview as value (value.id)}
					<div class="flex flex-col items-center gap-1 w-16 flex-shrink-0">
						<img
							src={api.getFileURL(value.preview_file_id ?? '', 'small')}
							alt={value.label}
							title={value.label}
							class="w-16 h-16 rounded border border-line object-cover"
						/>
						<span class="text-2xs text-fg-muted text-center truncate w-full" title={value.label}>{value.label}</span>
					</div>
				{/each}
			</div>
		</DetailSection>
	{/if}
</div>
