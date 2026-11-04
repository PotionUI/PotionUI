<script lang="ts">
	import { Badge, Button, SegmentedControl } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import NodeHeader from './NodeHeader.svelte';
	import NodeIssues from './NodeIssues.svelte';
	import NodeStatusRow from './NodeStatusRow.svelte';
	import OutputGallery from './OutputGallery.svelte';
	import TextOutputs from './TextOutputs.svelte';
	import type { Lane } from '$lib/elixirs/choice';
	import { openOnActivate } from '$lib/elixirs/nodeClick';

	interface Props {
		data: any;
		phone?: boolean;
		flash?: boolean;
	}

	let { data, phone = false, flash = false }: Props = $props();

	let kind = $derived(data.node.kind);
	let status = $derived(data.run?.status ?? null);
	let pending = $derived(['queued', 'running'].includes(status) ? data.run.pending : 0);
	let failedCount = $derived(data.run?.failedCount ?? 0);
	let entries = $derived(kind === 'media' ? data.assets : data.run?.outputs ?? []);
	let chips = $derived(data.choice?.choosing ? data.choice.lanes.filter((lane: Lane) => lane.open || lane.sent) : []);
	let activeChip = $derived(data.choice?.lanes.find((lane: Lane) => lane.edgeId === data.choice.active) ?? null);
	let border = $derived(data.panelOpen ? 'border-signal' : data.severity === 'error' || status === 'failed' ? 'border-danger' : 'border-line-strong');
	let showOutputs = $derived(kind === 'media' || (data.run && !['waiting', 'queued'].includes(status)));
</script>

<article
	class="node-card cursor-pointer rounded-lg border bg-surface-1 shadow-raised {border}"
	class:flash
	aria-label="{data.label}, {kind === 'media' ? 'files' : kind === 'llm' ? 'prompt writer' : 'preset step'}"
	data-state={status ?? 'idle'}
	data-card-id={data.node.id}
	data-node-root={data.node.id}
	tabindex="0"
	use:openOnActivate={() => data.onopennode(data.node.id)}
>
	<NodeHeader {data} {kind} {phone} />

	{#if kind === 'media'}
		<div class="files">
			<span class="font-mono tabular-nums text-fg">{data.countLine}</span>
			<span class="text-sm text-fg-muted">{data.sourceLine}</span>
		</div>
		<OutputGallery
			{entries}
			label="{data.label} files"
			emptyText="Choose files from History or the Library."
			openerScope={data.node.id}
			onopen={(entry, index) => data.onopen(data.node.id, entries, index)}
		/>
		{#if data.unused}
			<p class="warn">Nothing uses these files yet. Add a step after them.</p>
		{/if}
	{:else if kind === 'preset' && data.chooser}
		<div class="chooser" data-no-open>
			<span class="text-sm text-fg-muted">{data.chooser.title}</span>
			{#each data.chooser.options as option (`${option.preset_id}::${option.mode}`)}
				<button type="button" class="choice" onclick={() => data.onchoosepreset(data.node.id, option.preset_id, option.mode)}>
					<span class="min-w-0 truncate">{option.preset_name} · {option.mode_label}</span>
					<span class="shrink-0 text-sm text-fg-subtle">makes {option.makes}</span>
				</button>
			{:else}
				<p class="text-sm text-fg-subtle">No installed preset fits here. Choose one in the step settings.</p>
			{/each}
		</div>
	{:else}
		<div class="lead">
			{#if kind === 'llm'}
				<p class="instruction {data.instructionLine ? 'text-fg-muted' : 'text-fg-subtle'}">{data.instructionLine || 'No instruction yet'}</p>
			{:else}
				<p class="truncate text-fg">{data.presetLine}</p>
				{#if data.marker}
					<p class="marker {data.marker.tone}" data-marker={data.marker.tone}>
						{#if data.marker.icon}
							<Icon name={data.marker.icon} className="h-3.5 w-3.5 shrink-0" />
						{/if}
						<span class="truncate">{data.marker.text}</span>
						{#if data.edited}
							<Badge variant="signal" size="sm">Edited</Badge>
						{/if}
					</p>
				{/if}
			{/if}
		</div>
		{#if data.slots.length > 0 || kind === 'llm'}
			<div class="slots">
				{#each data.slots as slot (`${slot.kind}:${slot.port}`)}
					<div class="slot">
						<span class="slot-label">{slot.label}</span>
						<button
							type="button"
							class="pick"
							aria-haspopup="menu"
							aria-label="{slot.label}: {slot.valueLabel}. Change"
							disabled={data.readOnly}
							onclick={(event) => data.onslot(event.currentTarget, data.node.id, slot)}
						>
							{#if slot.writer}
								<span class="ai-icon small" aria-hidden="true"><Icon name="sparkles" className="h-2.5 w-2.5" /></span>
							{/if}
							<span class="value {slot.empty ? 'text-fg-subtle' : ''}">{slot.valueLabel}</span>
							<Icon name="chevron-down" className="h-3.5 w-3.5 text-fg-subtle" />
						</button>
					</div>
				{/each}
				{#if kind === 'llm'}
					<div class="slot">
						<span class="slot-label">Language model</span>
						<button
							type="button"
							class="pick"
							aria-haspopup="menu"
							aria-label="Language model: {data.modelLabel}. Change"
							disabled={data.readOnly}
							onclick={(event) => data.onmodel(event.currentTarget, data.node.id)}
						>
							<span class="value">{data.modelLabel}</span>
							<Icon name="chevron-down" className="h-3.5 w-3.5 text-fg-subtle" />
						</button>
					</div>
				{/if}
				{#if data.via}
					<p class="via">
						<Icon name="arrow-right" className="h-3.5 w-3.5 shrink-0" />
						<span>Files from <span class="em">{data.via.files}</span>, text from <span class="em">{data.via.text}</span></span>
					</p>
				{/if}
			</div>
		{/if}

		<NodeStatusRow
			view={data.run}
			idle={!data.run}
			label={data.statusLabel}
			tone={data.statusTone}
			stale={data.stale}
			staleReason={data.staleReason ?? 'An earlier step has newer outputs.'}
			rerunBlocked={data.runAgainBlocked}
			onrerun={data.readOnly ? null : () => data.onrunagain(data.node.id)}
			errors={data.run?.errors ?? []}
			canRetry={data.canRetry}
			busy={data.busy}
			onretry={() => data.onretry(data.node.id)}
		/>

		{#if chips.length > 0}
			<div class="pick-for">
				<div class="flex items-center justify-between gap-2">
					<span class="text-sm text-fg-muted">Pick for</span>
					{#if activeChip?.open}
						<span class="flex shrink-0 gap-1">
							<Button variant="ghost" size="sm" onclick={() => data.onselectall(data.node.id)}>Select all</Button>
							<Button variant="ghost" size="sm" onclick={() => data.onclear(data.node.id)}>Clear</Button>
						</span>
					{/if}
				</div>
				<div class="self-start">
					<SegmentedControl items={chips.map((lane: Lane) => ({ id: lane.edgeId, label: `${lane.label} ${lane.selected.length}/${lane.eligible.length}` }))} selected={data.choice.active} onSelect={(edgeId: string) => data.onlane(data.node.id, edgeId)} ariaLabel="Which step you are picking for" variant="toggle" />
				</div>
				{#if chips.length > 1}
					<span class="text-sm text-fg-subtle">Each step can continue with different {kind === 'llm' ? 'prompts' : 'files'}.</span>
				{/if}
				{#if activeChip?.error}
					<p class="rounded border border-warning/30 bg-warning/10 px-2 py-1.5 text-sm text-warning">{activeChip.error}</p>
				{/if}
			</div>
		{/if}

		{#if kind === 'llm'}
			<TextOutputs
				{entries}
				{pending}
				failed={status === 'failed' ? failedCount : 0}
				dim={data.stale}
				pinned={data.node.pinned ?? {}}
				sources={data.sources ?? {}}
				choice={data.choice}
				readOnly={data.readOnly}
				{phone}
				label="{data.label} prompts"
				stepName={data.label}
				ontick={(key) => data.ontick(data.node.id, key)}
				onedit={(entry, text) => data.onedittext(data.node.id, entry, text)}
				onpin={(entry) => data.onpintext(data.node.id, entry)}
				onunpin={(entry) => data.onunpintext(data.node.id, entry)}
			/>
			{#if data.targetMissing}
				<p class="warn">Nothing uses these prompts yet. Choose this writer on a step's Prompt.</p>
			{/if}
		{:else if showOutputs}
			<OutputGallery
				{entries}
				{pending}
				failed={status === 'failed' ? failedCount : 0}
				dim={data.stale}
				pendingKind={data.makes}
				choice={data.choice}
				openerScope={data.node.id}
				label="{data.label} outputs"
				ontick={(key) => data.ontick(data.node.id, key)}
				onopen={(entry, index) => data.onopen(data.node.id, entries, index)}
			/>
		{/if}
	{/if}

	{#if data.waiting}
		<div class="continue">
			<span class="text-fg-muted">
				<span class="font-mono tabular-nums text-fg">{data.waiting.lane.selected.length}</span> of
				<span class="font-mono tabular-nums text-fg">{data.waiting.lane.eligible.length}</span>
				<span class="text-signal">picked</span>
			</span>
			<Button variant="primary" size="sm" icon="play" disabled={data.waiting.lane.selected.length === 0} loading={data.continuing.has(data.waiting.lane.edgeId)} onclick={() => data.oncontinue(data.waiting.parentId, data.waiting.lane.edgeId)}>
				Continue
			</Button>
		</div>
	{/if}

	<NodeIssues issues={data.issues} />
</article>

<style>
	.node-card {
		width: 100%;
		font-size: 13px;
		line-height: 20px;
	}
	:global(:where(html:not([data-input='pointer']))) .node-card:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}
	.node-card.flash {
		animation: flash 1.4s cubic-bezier(0.25, 1, 0.5, 1);
	}
	@keyframes flash {
		0% {
			box-shadow: 0 0 0 2px rgb(var(--signal));
		}
		100% {
			box-shadow: 0 0 0 2px transparent;
		}
	}
	.lead {
		padding: 16px 16px 12px;
	}
	.marker {
		display: flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
		margin-top: 2px;
		font-size: 12px;
		line-height: 20px;
	}
	.marker.signal {
		color: rgb(var(--signal));
	}
	.marker.muted {
		color: rgb(var(--fg-muted));
	}
	.marker.subtle {
		color: rgb(var(--fg-subtle));
	}
	.marker.warning {
		color: rgb(var(--warning));
	}
	.instruction {
		display: -webkit-box;
		-webkit-line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
		white-space: pre-line;
	}
	.slots {
		display: flex;
		flex-direction: column;
		gap: 12px;
		padding: 0 16px 18px;
	}
	.slot {
		display: flex;
		align-items: center;
		gap: 14px;
	}
	.slot-label {
		width: 104px;
		flex: none;
		font-size: 12px;
		color: rgb(var(--fg-muted));
	}
	.pick {
		display: flex;
		height: 40px;
		min-width: 0;
		flex: 1;
		align-items: center;
		gap: 6px;
		padding: 0 8px;
		border: 1px solid rgb(var(--field-border));
		border-radius: 4px;
		background: rgb(var(--field-bg));
		color: rgb(var(--fg));
		text-align: left;
	}
	.pick:hover:not(:disabled) {
		border-color: rgb(var(--line-hover));
	}
	.pick:disabled {
		cursor: default;
		opacity: 0.7;
	}
	:global(:where(html:not([data-input='pointer']))) .pick:focus-visible,
	:global(:where(html:not([data-input='pointer']))) .choice:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}
	.value {
		min-width: 0;
		flex: 1;
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}
	.ai-icon {
		display: inline-grid;
		place-items: center;
		flex: none;
		border-radius: 4px;
		color: rgb(var(--on-scrim));
		background: linear-gradient(145deg, rgb(var(--ai-1)), rgb(var(--ai-2)) 55%, rgb(var(--ai-3)));
	}
	.ai-icon.small {
		width: 16px;
		height: 16px;
	}
	.via {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		font-size: 12px;
		line-height: 16px;
		color: rgb(var(--fg-subtle));
	}
	.via .em {
		font-weight: 600;
		color: rgb(var(--fg-muted));
	}
	.files {
		display: flex;
		flex-direction: column;
		padding: 16px 16px 4px;
	}
	.warn {
		margin: 0 16px 16px;
		padding: 6px 8px;
		border: 1px solid rgb(var(--warning) / 0.3);
		border-radius: 4px;
		background: rgb(var(--warning) / 0.08);
		color: rgb(var(--warning));
		font-size: 12px;
		line-height: 16px;
	}
	.chooser {
		display: flex;
		flex-direction: column;
		gap: 4px;
		max-height: 320px;
		overflow-y: auto;
		padding: 16px;
		cursor: default;
		scrollbar-width: thin;
	}
	.choice {
		display: flex;
		min-height: 44px;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 0 10px;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 4px;
		background: rgb(var(--surface-2));
		text-align: left;
		color: rgb(var(--fg));
	}
	.choice:hover {
		border-color: rgb(var(--line-hover));
	}
	.pick-for {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 14px 16px 12px;
		border-top: 1px solid rgb(var(--line));
	}
	.continue {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 12px 16px;
		border-top: 1px solid rgb(var(--line));
		font-size: 12px;
	}
</style>
