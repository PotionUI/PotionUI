<script lang="ts">
	import { Alert, Button, SegmentedControl } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import InlineChipEditor from '$lib/components/InlineChipEditor.svelte';
	import VariableManagerModal from '$lib/components/VariableManagerModal.svelte';
	import type { ChipData } from '$lib/types/segments';
	import type { VariableDef, VariablesMap } from '$lib/utils/variableDefs';
	import { countUsagesByName } from '$lib/utils/promptVariables';
	import { conditionReadersFor } from '$lib/utils/conditionTags';
	import { untrack } from 'svelte';
	import type { ElixirsApi } from '$lib/services/api/elixirs';
	import type { ElixirNode, FollowedState, MediaAsset, NodeOverrides, PresetEntry, ValidationIssue } from '$lib/elixirs/types';
	import MediaThumb from './MediaThumb.svelte';
	import NodeSettingsForm from './NodeSettingsForm.svelte';
	import NodeRequestPreview from './NodeRequestPreview.svelte';
	import { activeTab, panelTabs } from '$lib/elixirs/panelTabs';
	import { MEDIA_LABELS, countLabel, kindNoun, presetKey, summarizeSnapshot } from '$lib/elixirs/model';
	import { SESSION_MODES, conversions, effectiveSettings, modeExplanation, sessionMode, sessionProblem, sessionRow } from '$lib/elixirs/follow';

	type PanelNode = ElixirNode & { llm_id?: string | null; assets: MediaAsset[] };

	interface WriterModel {
		id: string;
		name: string;
		vision?: boolean;
	}

	interface WriterContext {
		models?: WriterModel[];
		targets?: Array<{ id: string; label: string }>;
		targetLabel?: string | null;
		problem?: string | null;
	}

	interface EditsPrompt {
		message: string;
		canKeep: boolean;
		resolve: (choice: 'keep' | 'discard' | null) => void;
	}

	interface Props {
		node: PanelNode;
		client: ElixirsApi;
		label: string;
		presets: PresetEntry[];
		editsPrompt?: EditsPrompt | null;
		onrename: (title: string | null) => void;
		onsettingschange: (overrides: NodeOverrides) => void;
		onchoosepreset: (presetId: string, mode: string) => void;
		followedState?: FollowedState;
		issues?: ValidationIssue[];
		sessionBusy?: boolean;
		onsessionmode?: (mode: string) => void;
		onpicksession?: (purpose: 'copy' | 'follow') => void;
		onkeepcopy?: () => void;
		onfollowsource?: () => void;
		onupdatefromsession?: () => void;
		onsaveassession?: () => void;
		onpickmedia: (kind: 'history' | 'library') => void;
		onremoveasset: (index: number) => void;
		onclearmedia?: () => void;
		onremove?: () => void;
		onclose?: () => void;
		writer?: WriterContext | null;
		onwriterchange?: (changes: Record<string, unknown>) => void;
		tab?: string;
		ontabchange?: (id: string) => void;
	}

	let {
		node,
		client,
		label,
		presets,
		editsPrompt = null,
		onrename,
		onsettingschange,
		onchoosepreset,
		followedState = undefined,
		issues = [],
		sessionBusy = false,
		onsessionmode,
		onpicksession,
		onkeepcopy,
		onfollowsource,
		onupdatefromsession,
		onsaveassession,
		onpickmedia,
		onremoveasset,
		onclearmedia,
		onremove,
		onclose,
		writer = null,
		onwriterchange,
		tab = 'quick',
		ontabchange
	}: Props = $props();

	let writerModels = $derived(writer?.models ?? []);
	let writerDefaultLabel = $derived(writerModels.length > 0 ? `Default (${writerModels[0].name})` : 'Default');

	let title = $state(untrack(() => node.title ?? ''));
	let instruction = $state(untrack(() => node.instruction ?? ''));
	let instructionChips = $state.raw<Record<string, ChipData>>(untrack(() => node.instruction_chips ?? {}));
	let instructionTimer: ReturnType<typeof setTimeout> | undefined;
	let instructionDirty = false;
	let titleDirty = false;

	$effect(() => {
		const nextInstruction = node.instruction ?? '';
		const nextChips = node.instruction_chips ?? {};
		const nextTitle = node.title ?? '';
		untrack(() => {
			if (!instructionDirty) {
				if (instruction !== nextInstruction) instruction = nextInstruction;
				if (JSON.stringify(instructionChips) !== JSON.stringify(nextChips)) instructionChips = nextChips;
			}
			if (!titleDirty && title !== nextTitle) title = nextTitle;
		});
	});
	const NO_VARIABLES: VariablesMap = {};
	let writerVariables = $derived(node.variables ?? NO_VARIABLES);
	let writerVariableCount = $derived(Object.keys(writerVariables).length);
	let variablesOpen = $state(false);
	let writerUsageCounts = $derived(countUsagesByName([instruction]));
	let writerReaders = $derived(conditionReadersFor([instruction]));

	function changeWriterVariables(variables: VariablesMap) {
		onwriterchange?.({ variables });
	}

	function changeWriterVariable(name: string, def: VariableDef) {
		changeWriterVariables({ ...writerVariables, [name]: def });
	}

	function commitInstruction() {
		clearTimeout(instructionTimer);
		if (!instructionDirty) return;
		instructionDirty = false;
		const chipsChanged = JSON.stringify(instructionChips) !== JSON.stringify(node.instruction_chips ?? {});
		if (instruction !== (node.instruction ?? '') || chipsChanged) onwriterchange?.({ instruction, instruction_chips: instructionChips });
	}

	function typeInstruction(event: CustomEvent<{ value: string; chips: Record<string, ChipData> }>) {
		if (event.detail.value === instruction && JSON.stringify(event.detail.chips ?? {}) === JSON.stringify(instructionChips)) return;
		instruction = event.detail.value;
		instructionChips = event.detail.chips ?? {};
		instructionDirty = true;
		clearTimeout(instructionTimer);
		instructionTimer = setTimeout(commitInstruction, 600);
	}

	let pinnedCount = $derived(node.kind === 'llm' ? Object.keys(node.pinned ?? {}).length : 0);
	let formKey = $state(0);
	let tabs = $derived(panelTabs(node, true));
	let currentTab = $derived(activeTab(tabs, tab));
	let showQuick = $derived(tabs.length === 0 || currentTab === 'quick');
	let showSettings = $derived(tabs.length === 0 || currentTab === 'settings');

	function withoutForm(overrides: NodeOverrides | undefined) {
		const next = { ...(overrides || {}) };
		delete next.formData;
		return next;
	}
	let mode = $derived(sessionMode(node));
	let following = $derived(mode === 'follow');
	let summary = $derived(
		node.kind !== 'preset'
			? null
			: following
				? summarizeSnapshot(effectiveSettings(node, followedState), {})
				: summarizeSnapshot(mode === 'copy' ? node.session_snapshot : {}, node.overrides)
	);
	let problem = $derived(sessionProblem(node, followedState, issues));
	let row = $derived(sessionRow(node, followedState));
	let actions = $derived(conversions(node));
	let followedName = $derived(row?.name ?? 'the session');
	let selectedPreset = $derived(node.kind === 'preset' && node.preset_id ? presetKey(node.preset_id, node.mode) : '');
	let presetGroups = $derived.by(() => {
		const groups = new Map<string, PresetEntry[]>();
		for (const preset of presets) {
			if (!groups.has(preset.preset_name)) groups.set(preset.preset_name, []);
			groups.get(preset.preset_name)?.push(preset);
		}
		return [...groups.entries()];
	});
	let knownPreset = $derived(!selectedPreset || presets.some((preset) => presetKey(preset.preset_id, preset.mode) === selectedPreset));

	function commitTitle() {
		if (!titleDirty) return;
		titleDirty = false;
		const next = title.trim();
		if (next !== (node.title ?? '')) onrename(next || null);
	}

	export function flush() {
		commitInstruction();
		commitTitle();
	}

	function onPresetChange(event: Event & { currentTarget: HTMLSelectElement }) {
		const value = event.currentTarget.value;
		if (!value) return;
		const preset = presets.find((item) => presetKey(item.preset_id, item.mode) === value);
		if (preset) onchoosepreset(preset.preset_id, preset.mode);
	}
</script>

<aside class="flex h-full min-h-0 w-full flex-col bg-surface-1" aria-label="{label} settings">
	<header class="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
		<div class="min-w-0">
			<p class="font-mono text-sm uppercase tracking-wide text-fg-subtle">{kindNoun(node.kind)}</p>
			<h2 class="truncate text-md font-semibold text-fg">{label}</h2>
		</div>
		<Tooltip text="Close settings">
			<button type="button" class="panel-icon" aria-label="Close node settings" onclick={onclose}>
				<Icon name="close" className="h-4 w-4" />
			</button>
		</Tooltip>
	</header>

	{#if tabs.length > 0}
		<div class="border-b border-line px-4 py-2.5">
			<SegmentedControl items={tabs} selected={currentTab} onSelect={(id) => ontabchange?.(id)} ariaLabel="Node settings sections" variant="toggle" fullWidth />
		</div>
	{/if}

	<div class="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 py-4">
		{#if showQuick}
		<div class="flex flex-col gap-1.5">
			<label for="elixir-node-title" class="text-sm font-medium text-fg-muted">Name</label>
			<input
				id="elixir-node-title"
				class="input field w-full"
				maxlength="120"
				placeholder={node.kind === 'media' ? 'Product photos' : node.kind === 'llm' ? 'Describe for Flux' : 'Orbit left'}
				bind:value={title}
				oninput={() => (titleDirty = true)}
				onblur={commitTitle}
				onkeydown={(event) => event.key === 'Enter' && event.currentTarget.blur()}
			/>
		</div>
		{/if}

		{#if node.kind === 'llm'}
			<div class="flex flex-col gap-1.5">
				<div class="flex items-center justify-between gap-2">
					<span id="elixir-node-instruction-label" class="text-sm font-medium text-fg-muted">Instruction</span>
					<Button variant="secondary" size="sm" icon="braces" onclick={() => (variablesOpen = true)}>
						Variables
						{#if writerVariableCount > 0}
							<span class="font-mono tabular-nums text-signal">{writerVariableCount}</span>
						{/if}
					</Button>
				</div>
				<div
					class="instruction-box segment-composer"
					role="group"
					aria-labelledby="elixir-node-instruction-label"
					data-testid="elixir-node-instruction"
					onfocusout={commitInstruction}
				>
					<div class="segment-content">
						<InlineChipEditor
							value={instruction}
							chips={instructionChips}
							borderless={true}
							variant="segment-composer"
							variables={writerVariables}
							conditionReaders={writerReaders}
							onVariableDefChange={changeWriterVariable}
							onOpenVariableManager={() => (variablesOpen = true)}
							placeholder="For the provided image, write a prompt that recreates it."
							on:change={typeInstruction}
						/>
					</div>
				</div>
				<p class="text-sm text-fg-subtle">
					Runs once for each connected image or video, or once when nothing is connected. Type $ for one of its variables, # for the phrasebook, or {'{a|b}'} for a choice; every prompt it writes gets its own roll.
				</p>
			</div>

			<div class="flex flex-col gap-1.5">
				<label for="elixir-node-target" class="text-sm font-medium text-fg-muted">Writes for</label>
				<select
					id="elixir-node-target"
					class="input field w-full"
					value={node.target_node_id ?? ''}
					onchange={(event) => onwriterchange?.({ target_node_id: event.currentTarget.value || null })}
				>
					<option value="">The preset its text goes to</option>
					{#each writer?.targets ?? [] as target (target.id)}
						<option value={target.id}>{target.label}</option>
					{/each}
				</select>
				<p class="text-sm text-fg-subtle">
					{writer?.targetLabel
						? `Follows the prompt style of ${writer.targetLabel} and the model in its session.`
						: 'No preset yet, so the prompt is not tuned to a model.'}
				</p>
			</div>

			<section class="flex flex-col gap-2" aria-label="Language model">
				<label for="elixir-node-llm" class="text-sm font-medium text-fg-muted">Language model</label>
				<select
					id="elixir-node-llm"
					class="input field w-full"
					value={node.llm_id ?? ''}
					onchange={(event) => onwriterchange?.({ llm_id: event.currentTarget.value || null })}
				>
					<option value="">{writerDefaultLabel}</option>
					{#if node.llm_id && !writerModels.some((model) => model.id === node.llm_id)}
						<option value={node.llm_id} disabled>Not available any more</option>
					{/if}
					{#each writerModels as model (model.id)}
						<option value={model.id}>{model.vision ? `${model.name} · sees images` : model.name}</option>
					{/each}
				</select>
				{#if writer?.problem}
					<Alert variant="warning" density="compact">{writer.problem}</Alert>
				{:else}
					<p class="text-sm text-fg-subtle">Uses a language model you can use, one that can see images when files are connected. A video is shown as a few of its frames.</p>
				{/if}
			</section>

			<section class="flex flex-col gap-2" aria-label="Pinned prompts">
				<div class="flex items-center justify-between gap-2">
					<h3 class="text-sm font-medium text-fg-muted">Pinned prompts</h3>
					<span class="font-mono text-sm tabular-nums text-fg-subtle">{pinnedCount}</span>
				</div>
				<p class="text-sm text-fg-subtle">
					{pinnedCount > 0
						? 'Pinned prompts are used again instead of asking the language model, for the same file.'
						: 'Pin or edit a written prompt on the node to keep it for the next brews.'}
				</p>
				{#if pinnedCount > 0}
					<div>
						<Button variant="secondary" size="sm" icon="pin" onclick={() => onwriterchange?.({ pinned: {} })}>Unpin all</Button>
					</div>
				{/if}
			</section>
		{:else if node.kind === 'preset'}
			{#if showQuick}
			<div class="flex flex-col gap-1.5">
				<label for="elixir-node-preset" class="text-sm font-medium text-fg-muted">Preset and mode</label>
				<select id="elixir-node-preset" class="input field w-full" value={knownPreset ? selectedPreset : '__missing'} onchange={onPresetChange}>
					<option value="" disabled>Choose a preset</option>
					{#if !knownPreset}
						<option value="__missing" disabled>Not available here any more</option>
					{/if}
					{#each presetGroups as [name, modes] (name)}
						<optgroup label={name}>
							{#each modes as preset (presetKey(preset.preset_id, preset.mode))}
								<option value={presetKey(preset.preset_id, preset.mode)}>{preset.preset_name} · {preset.mode_label}</option>
							{/each}
						</optgroup>
					{/each}
				</select>
				{#if presets.length === 0}
					<p class="text-sm text-fg-subtle">No installed preset can be used in an elixir yet.</p>
				{/if}
			</div>

			{#if node.preset_id && node.mode}
				<section class="flex flex-col gap-2" aria-label="Session" data-session-mode={mode}>
					<h3 class="text-sm font-medium text-fg-muted">Session</h3>
					<SegmentedControl items={SESSION_MODES} selected={mode ?? ''} onSelect={(next) => !sessionBusy && onsessionmode?.(next)} ariaLabel="Where this step takes its settings from" variant="toggle" fullWidth />
					<p class="text-sm text-fg-subtle">{modeExplanation(mode)}</p>

					{#if problem}
						<Alert variant="warning" density="compact" title={problem.title}>{problem.message}</Alert>
						<div class="flex flex-wrap gap-2">
							<Button variant="secondary" size="sm" disabled={sessionBusy} onclick={() => onpicksession?.('follow')}>Pick another session</Button>
							<Button variant="ghost" size="sm" disabled={sessionBusy} onclick={onkeepcopy}>Keep a copy</Button>
						</div>
					{/if}

					{#if row}
						<button
							type="button"
							class="session-row"
							aria-haspopup="dialog"
							aria-label="{following ? 'Followed session' : 'Copied from'}: {row.name}{row.version ? `, version ${row.version}` : ''}. Choose another"
							disabled={sessionBusy}
							onclick={() => onpicksession?.(following ? 'follow' : 'copy')}
						>
							<span class="min-w-0 flex-1 truncate">{row.name}</span>
							{#if row.version}
								<span class="shrink-0 font-mono text-sm tabular-nums text-fg-subtle">v{row.version}</span>
							{/if}
							<Icon name="chevron-down" className="h-4 w-4 shrink-0 text-fg-subtle" />
						</button>
					{/if}

					{#if editsPrompt}
						<Alert variant="warning" density="compact" title="This step has local edits">
							{editsPrompt.message}
						</Alert>
						<div class="flex flex-wrap gap-2">
							{#if editsPrompt.canKeep}
								<Button variant="secondary" size="sm" onclick={() => editsPrompt.resolve('keep')}>Keep edits</Button>
							{/if}
							<Button variant="danger" size="sm" onclick={() => editsPrompt.resolve('discard')}>Discard edits</Button>
							<Button variant="ghost" size="sm" onclick={() => editsPrompt.resolve(null)}>Cancel</Button>
						</div>
					{:else if actions.length > 0}
						<div class="flex flex-wrap gap-2">
							{#if actions.includes('copy')}
								<Button variant="secondary" size="sm" icon="document" disabled={sessionBusy} onclick={onkeepcopy}>Make a copy</Button>
							{/if}
							{#if actions.includes('update')}
								<Button variant="secondary" size="sm" icon="refresh" disabled={sessionBusy} onclick={onupdatefromsession}>Update from session</Button>
							{/if}
							{#if actions.includes('follow')}
								<Button variant="secondary" size="sm" icon="plug" disabled={sessionBusy} onclick={onfollowsource}>Follow this session</Button>
							{/if}
							{#if actions.includes('save')}
								<Button variant="secondary" size="sm" icon="save" disabled={sessionBusy} onclick={onsaveassession}>Save as session</Button>
							{/if}
						</div>
					{/if}
				</section>
			{/if}
			{/if}

			{#if summary && showSettings}
				{#if following}
					<p class="read-only-note">
						<Icon name="lock" className="h-4 w-4 shrink-0" />
						<span>{problem ? 'These are the last settings this step saw. Keep a copy to edit them here.' : `These settings come from ${followedName}. Edit this in the session.`}</span>
					</p>
				{/if}
				{#if following}
				<section class="flex flex-col gap-2" aria-label={following ? 'Session settings' : mode === 'copy' ? 'Copied settings' : 'Own settings'}>
					<h3 class="text-sm font-medium text-fg-muted">{following ? 'Session settings' : mode === 'copy' ? 'Copied settings' : 'Own settings'}</h3>
					<dl class="flex flex-col gap-2 text-sm">
						<div>
							<dt class="text-fg-subtle">Prompt</dt>
							<dd class="line-clamp-4 whitespace-pre-line text-fg">{summary.prompt || 'No prompt'}</dd>
						</div>
						{#if summary.negative}
							<div>
								<dt class="text-fg-subtle">Negative prompt</dt>
								<dd class="line-clamp-2 text-fg">{summary.negative}</dd>
							</div>
						{/if}
						<div class="grid grid-cols-2 gap-2">
							<div>
								<dt class="text-fg-subtle">Seed</dt>
								<dd class="font-mono tabular-nums text-fg">{summary.seed === null || summary.seed === -1 ? 'Random' : summary.seed}</dd>
							</div>
							{#if summary.director}
								<div>
									<dt class="text-fg-subtle">Video Director</dt>
									<dd class="font-mono text-fg">{summary.director.mode ?? 'On'}</dd>
								</div>
							{/if}
							{#if summary.variables > 0}
								<div>
									<dt class="text-fg-subtle">Variables</dt>
									<dd class="font-mono tabular-nums text-fg">{summary.variables}</dd>
								</div>
							{/if}
						</div>
						{#if summary.settings.length > 0}
							<div>
								<dt class="text-fg-subtle">Settings</dt>
								<dd>
									<ul class="mt-1 flex flex-col divide-y divide-line rounded border border-line">
										{#each summary.settings as setting (setting.name)}
											<li class="flex min-w-0 items-center justify-between gap-3 px-2.5 py-1">
												<span class="truncate font-mono text-fg-muted">{setting.name}</span>
												<span class="truncate font-mono tabular-nums text-fg">{setting.value}</span>
											</li>
										{/each}
									</ul>
								</dd>
							</div>
						{/if}
						{#if summary.media.length > 0}
							<div>
								<dt class="text-fg-subtle">Saved media and lists</dt>
								<dd class="font-mono text-fg">{summary.media.join(', ')}</dd>
							</div>
						{/if}
					</dl>
				</section>
				{:else}
				<section class="flex flex-col gap-2" aria-label="Node settings">
					{#if node.overrides?.formData && mode === 'copy'}
						<div class="flex justify-end">
							<Button variant="ghost" size="sm" icon="undo" onclick={() => { formKey += 1; onsettingschange(withoutForm(node.overrides)); }}>
								Reset to session
							</Button>
						</div>
					{/if}
					{#key `${node.preset_id}:${node.mode}:${(node.session_source as { imported_at?: string } | null | undefined)?.imported_at}:${formKey}`}
						<NodeSettingsForm {node} onchange={onsettingschange} />
					{/key}
				</section>
				{/if}

				<NodeRequestPreview {node} {client} followed={followedState} />
			{/if}

		{:else}
			<section class="flex flex-col gap-2" aria-label="Files">
				<div class="flex items-center justify-between gap-2">
					<h3 class="text-sm font-medium text-fg-muted">Files</h3>
					<span class="font-mono text-sm tabular-nums text-fg-subtle">
						{countLabel(node.assets.length, node.media_type === 'video' ? 'video' : node.media_type === 'image' ? 'image' : 'file')}
					</span>
				</div>
				<p class="text-sm text-fg-subtle">
					{node.media_type
						? `This source holds ${MEDIA_LABELS[node.media_type].toLowerCase()}. Remove them all to switch type.`
						: 'Pick images or videos. One source holds one kind; add another source for the other.'}
				</p>
				<div class="flex flex-wrap gap-2">
					<Button variant="secondary" size="sm" icon="clock" onclick={() => onpickmedia('history')}>From History</Button>
					<Button variant="secondary" size="sm" icon="library" onclick={() => onpickmedia('library')}>From Library</Button>
					{#if node.assets.length > 0}
						<Button variant="ghost" size="sm" onclick={onclearmedia}>Remove all</Button>
					{/if}
				</div>
				{#if node.assets.length > 0}
					<ul class="grid grid-cols-3 gap-2" aria-label="Selected files">
						{#each node.assets as asset, index (`${asset.kind}:${asset.item_id ?? asset.file_id}`)}
							<li class="relative">
								<MediaThumb {asset} />
								<Tooltip text="Remove from this source">
									<button
										type="button"
										class="remove-asset"
										aria-label="Remove {asset.original_filename || `file ${index + 1}`}"
										onclick={() => onremoveasset(index)}
									>
										<Icon name="close" className="h-3.5 w-3.5" />
									</button>
								</Tooltip>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		{/if}
	</div>

	{#if showQuick}
		<footer class="border-t border-line px-4 py-3">
			<Button variant="ghost" size="sm" icon="trash" onclick={onremove}>Remove node</Button>
		</footer>
	{/if}
</aside>

{#if node.kind === 'llm'}
	<VariableManagerModal
		isOpen={variablesOpen}
		variables={writerVariables}
		usageCounts={writerUsageCounts}
		readers={writerReaders}
		on:close={() => (variablesOpen = false)}
		on:change={(event) => changeWriterVariables(event.detail)}
	/>
{/if}

<style>
	.panel-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 2rem;
		min-height: 2rem;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
	}
	.panel-icon:hover {
		background: rgb(var(--surface-3));
		color: rgb(var(--fg));
	}
	.field {
		height: 40px;
		min-height: 40px;
	}
	.session-row {
		display: flex;
		width: 100%;
		height: 40px;
		align-items: center;
		gap: 8px;
		padding: 0 8px 0 12px;
		border: 1px solid rgb(var(--field-border));
		border-radius: 4px;
		background: rgb(var(--field-bg));
		color: rgb(var(--fg));
		text-align: left;
	}
	.session-row:hover:not(:disabled) {
		border-color: rgb(var(--line-hover));
		background: rgb(var(--surface-3));
	}
	.session-row:disabled {
		cursor: default;
		opacity: 0.6;
	}
	:global(:where(html:not([data-input='pointer']))) .session-row:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}
	.instruction-box {
		min-height: 8rem;
		padding: 6px 10px;
		border: 1px solid rgb(var(--line));
		border-radius: 4px;
		background: rgb(var(--surface-2));
	}
	.instruction-box:focus-within {
		border-color: rgb(var(--line-strong));
	}
	.read-only-note {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		padding: 8px 10px;
		border: 1px solid rgb(var(--line));
		border-radius: 4px;
		background: rgb(var(--surface-2));
		color: rgb(var(--fg-muted));
		font-size: 12px;
		line-height: 16px;
	}
	.remove-asset {
		position: absolute;
		top: 0.25rem;
		right: 0.25rem;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.5rem;
		height: 1.5rem;
		border-radius: 4px;
		background: rgb(var(--canvas) / 0.8);
		color: rgb(var(--fg));
	}
	.remove-asset:hover {
		background: rgb(var(--danger-solid));
	}
</style>
