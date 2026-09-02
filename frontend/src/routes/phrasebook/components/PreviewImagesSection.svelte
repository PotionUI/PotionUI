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
	import HelpButton from '$lib/components/HelpButton.svelte';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import { Button, Alert, Switch } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import type { TutorialSection } from '$lib/types/tutorial';
	import type { Segment } from '$lib/types/segments';

	// Owns the whole "Preview images" section of the category detail panel:
	// the explainer well over the Values-pane selection, the generation form
	// (extracted verbatim from the former PreviewGenerationPanel), the queue
	// summary/action, and the strip of values that already have a preview.
	let { categoryId }: { categoryId: string } = $props();

	let gen = $derived($previewGenerationStore);
	let values = $derived($selectedCategoryValues);
	let missingIds = $derived($valuesWithoutPreviewIds);
	let withPreview = $derived($previewCount);
	let selectedPresetName = $derived(gen.presets.find((p) => p.id === gen.selectedPresetId)?.name ?? '');
	let selectedModeLabel = $derived(gen.modes.find((m) => m.name === gen.selectedMode)?.label ?? gen.selectedMode);
	let valuesWithPreview = $derived(values.filter((v) => v.preview_file_id));

	const previewTutorialContent: TutorialSection[] = [
		{
			title: 'What is Preview Generation?',
			content: 'Preview generation automatically creates images for your phrasebook values. Each value will get a preview image that helps users understand what selecting that option will produce.',
			icon: 'info'
		},
		{
			title: 'Step 1: Create a Session',
			content: 'Before generating previews, you need to **save a session** on the Generation page. Sessions store your generation settings (model, steps, CFG, etc.). Go to the Generation page, configure your settings, and click "Save Session".',
			variant: 'tip'
		},
		{
			title: 'Step 2: Select Values',
			content: 'Use the **checkboxes** in the values list to select which values should get preview images. You can use "Select All" or "Deselect All" to quickly manage selections. Only *active* values can be selected.',
			icon: 'check'
		},
		{
			title: 'Step 3: Configure the Template',
			content: 'The prompt template uses `<< value >>` as a placeholder. For example, if your template is "A photo of << value >>" and you have a value "sunset", the actual prompt becomes "A photo of sunset".',
			variant: 'tip'
		},
		{
			title: 'Understanding Generation',
			content: 'Previews are generated **sequentially** - one at a time. The process can take several minutes depending on how many values you selected. You can continue using other parts of the app while generation runs in the background.',
			variant: 'warning'
		}
	];

	function handleGeneratePreviews() {
		previewGenerationStore.handleGeneratePreviews(categoryId, $phrasebookStore.selectedValueIds);
	}

	function handlePromptSegmentsChange(event: CustomEvent<Segment[]>) {
		previewGenerationStore.setPromptSegments(event.detail);
	}
</script>

<DetailSection label="Preview images">
	{#snippet headerExtra()}
		<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
			{withPreview} / {values.length} HAVE A PREVIEW
		</span>
	{/snippet}

	<div class="flex flex-col gap-4">
		<div class="flex gap-2.5 rounded-lg bg-surface-3 shadow-well p-3">
			<Icon name="info" className="w-4 h-4 text-signal flex-shrink-0 mt-0.5" />
			<div class="flex-1 text-xs text-fg-muted leading-relaxed">
				Generate a real image for each value so you can see what it does.
				{#if $selectedCount === 0}
					Select values in the Values pane first.
				{:else}
					<span class="text-fg font-semibold">One generation is queued per value selected in the Values pane</span>
					&mdash; currently <span class="text-fg font-semibold">{$selectedCount} of {values.length}</span> selected.
				{/if}
				<div class="mt-2 flex gap-4">
					<button
						type="button"
						class="text-2xs text-fg-muted underline decoration-line-strong hover:text-fg hover:decoration-fg-muted"
						onclick={() => phrasebookStore.selectAllValues()}
					>
						Select all {values.length}
					</button>
					{#if missingIds.length > 0}
						<button
							type="button"
							class="text-2xs text-fg-muted underline decoration-line-strong hover:text-fg hover:decoration-fg-muted"
							onclick={() => phrasebookStore.selectValuesWithoutPreview()}
						>
							Select missing ({missingIds.length})
						</button>
					{/if}
				</div>
			</div>
		</div>

		<div class="flex flex-col gap-4 {$selectedCount === 0 ? 'opacity-45 pointer-events-none' : ''}">
			<div class="flex items-center justify-between">
				<span class="font-mono text-2xs font-semibold uppercase tracking-[0.13em] text-fg-subtle">Target</span>
				<HelpButton title="Preview Generation Help" tutorialContent={previewTutorialContent} />
			</div>
			<div class="grid grid-cols-2 gap-3">
				<div>
					<label for="preview-preset" class="block text-xs text-fg-muted mb-1.5">Preset</label>
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
					<label for="preview-session" class="block text-xs text-fg-muted mb-1.5">Session</label>
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
					<p class="text-2xs text-fg-subtle mt-1">Sessions carry the model, steps, CFG and size</p>
				</div>
			</div>
			<div>
				<label for="preview-mode" class="block text-xs text-fg-muted mb-1.5">Mode</label>
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

			<div>
				<span class="block font-mono text-2xs font-semibold uppercase tracking-[0.13em] text-fg-subtle mb-1.5">Prompt template</span>
				<SegmentedPromptEditor
					segments={gen.promptSegments}
					label="Prompt template"
					compact
					on:segmentsChange={handlePromptSegmentsChange}
				/>
				<div class="flex items-center gap-2 mt-1.5">
					<code class="text-xs px-1.5 py-0.5 bg-signal/10 text-signal rounded font-mono">{'<< value >>'}</code>
					<span class="text-xs text-fg-subtle">is replaced by each value&rsquo;s text</span>
				</div>
			</div>

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

		{#if gen.isGeneratingPreviews}
			{#if gen.previewBatchProgress}
				<div class="h-1 rounded bg-surface-3 overflow-hidden">
					<div
						class="h-full bg-signal-solid rounded"
						style="width: {gen.previewBatchProgress.total > 0 ? (gen.previewBatchProgress.done / gen.previewBatchProgress.total) * 100 : 0}%"
					></div>
				</div>
			{/if}
			<Button variant="primary" class="w-full" loading>Generating&hellip;</Button>
			{#if gen.previewGenerationStatus}
				<p class="text-xs text-fg-subtle text-center">{gen.previewGenerationStatus}</p>
			{/if}
		{:else}
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">
				<span class="tabular-nums">{$selectedCount}</span> generation{$selectedCount !== 1 ? 's' : ''}
				{#if selectedPresetName} &middot; {selectedPresetName}{/if}
				{#if selectedModeLabel} &middot; {selectedModeLabel}{/if}
			</span>

			{#if gen.previewGenerationStatus}
				<Alert variant={gen.previewGenerationStatus.includes('Error') ? 'danger' : 'signal'} density="compact">
					{gen.previewGenerationStatus}
				</Alert>
			{/if}

			<Button
				variant="primary"
				icon="sparkles"
				class="w-full"
				disabled={!gen.selectedSessionId || !gen.selectedMode || gen.sessions.length === 0 || $selectedCount === 0}
				onclick={handleGeneratePreviews}
			>
				Generate <span class="tabular-nums">{$selectedCount}</span> preview{$selectedCount !== 1 ? 's' : ''}
			</Button>
		{/if}

		{#if withPreview > 0}
			<div class="border-t border-line pt-4">
				<span class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-2.5">
					Existing previews &middot; {withPreview} of {values.length}
				</span>
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
				<p class="mt-3 text-xs text-fg-subtle">New previews are attached to their value automatically when done.</p>
			</div>
		{/if}
	</div>
</DetailSection>
