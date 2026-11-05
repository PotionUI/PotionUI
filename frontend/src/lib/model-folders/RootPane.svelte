<script lang="ts">
	import { Alert, Badge, Button, IconButton, Menu } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import { DetailBody, DetailHeader } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { isMobile } from '$lib/stores/viewport';
	import { indexingStatusStore } from '$lib/models-location/indexingStatus.svelte';
	import { digestPhase } from '$lib/models-location/indexingDisplay';
	import { profileBadgeLabel } from '$lib/model-roots/logic';
	import { listModelLayouts } from '$lib/model-roots/state.svelte';
	import type { ModelLayoutSummary, ModelRoot, ModelRootBinding } from '$lib/services/api/models';
	import AgainPanel from './AgainPanel.svelte';
	import BindingPanel from './BindingPanel.svelte';
	import PathText from './PathText.svelte';
	import RootPanel from './RootPanel.svelte';
	import StateMark from './StateMark.svelte';
	import type { FoldersController } from './controller.svelte';
	import {
		agoText,
		bindingFiles,
		bindingKey,
		formatBytes,
		formatCount,
		rootBytes,
		rootFiles,
		rootUnindexed,
		supportsHeaderScan,
		typeLabel
	} from './derive';

	let { ctrl, root: r, onBack }: { ctrl: FoldersController; root: ModelRoot; onBack?: () => void } = $props();

	type Sel = { kind: 'root' } | { kind: 'binding'; type: string; subdir: string };
	let sel = $state<Sel | null>(null);
	let catalog = $state<ModelLayoutSummary[]>([]);
	void listModelLayouts().then((l) => (catalog = l));

	const COLS = 'grid grid-cols-[minmax(0,1fr)_auto_32px] md:grid-cols-[minmax(0,.9fr)_minmax(0,1.4fr)_60px_80px_minmax(150px,1.1fr)_32px] items-center gap-x-3';

	const running = $derived(digestPhase(indexingStatusStore.status) === 'running');
	const selBinding = $derived.by(() => {
		const s = sel;
		return s && s.kind === 'binding' ? r.bindings.find((x) => x.model_type === s.type && x.subdir === s.subdir) : undefined;
	});
	const isSelB = (b: ModelRootBinding) => sel?.kind === 'binding' && sel.type === b.model_type && sel.subdir === b.subdir;

	const rootId = $derived(r.id);
	$effect(() => {
		void rootId;
		sel = null;
	});

	function rootChips() {
		const out: { key: string; label: string; tone?: 'neutral' | 'warning' }[] = [];
		if (r.kind === 'home') out.push({ key: 'home', label: 'Built-in' });
		if (r.layout_profile) out.push({ key: 'profile', label: profileBadgeLabel(r.layout_profile, catalog) });
		if (r.read_only) out.push({ key: 'ro', label: 'Read-only', tone: 'warning' });
		return out;
	}
	function selectBinding(b: ModelRootBinding) {
		sel = { kind: 'binding', type: b.model_type, subdir: b.subdir };
	}
	function rowMenu(b: ModelRootBinding): MenuItem[] {
		return [
			{ id: 'details', label: 'Details', icon: 'info' },
			...(!b.is_write && !r.read_only ? [{ id: 'dl', label: 'Use for downloads', icon: 'download' }] : []),
			...(supportsHeaderScan(b) ? [{ id: 'scan', label: b.scan_headers ? 'Stop detecting type from file' : 'Detect type from file', icon: 'cpu' }] : []),
			...(r.kind === 'home' ? [] : [{ id: 'sep', label: '', separator: true }, { id: 'rm', label: 'Remove from folder', icon: 'trash-2', danger: true }])
		];
	}
	async function onRow(b: ModelRootBinding, id: string) {
		if (id === 'details') selectBinding(b);
		else if (id === 'dl') await ctrl.makeDownloads(r, b);
		else if (id === 'scan') await ctrl.toggleScan(r, b, !b.scan_headers);
		else if (id === 'rm') await ctrl.removeBinding(r, b);
	}
	function key(e: KeyboardEvent, fn: () => void) {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			fn();
		}
	}
</script>

{#snippet statusBadges(b: ModelRootBinding)}
	{#if b.is_write}<Tooltip text="New {typeLabel(b.model_type)} downloads are saved here." position="top"><Badge variant="signal" size="sm">Downloads</Badge></Tooltip>{/if}
	{#if !b.exists}<Tooltip text="This folder is missing on disk" position="top"><Badge variant="warning" size="sm">Missing</Badge></Tooltip>{/if}
	{#if b.unindexed > 0}
		<Tooltip text="{formatCount(b.unindexed)} not indexed yet" position="top"><Badge variant={running ? 'signal' : 'neutral'} size="sm">{running ? 'Indexing' : `${formatCount(b.unindexed)} pending`}</Badge></Tooltip>
	{/if}
	{#if b.scan_headers}<Tooltip text="Type is detected from each file header" position="top"><Badge variant="neutral" size="sm">Header</Badge></Tooltip>{/if}
	{#if r.read_only && !b.is_write}<Badge variant="neutral" size="sm">Read-only</Badge>{/if}
{/snippet}

<DetailHeader
	title={r.label}
	icon={r.kind === 'home' ? 'folder' : 'server'}
	chipItems={rootChips()}
	backLabel={$isMobile ? 'Models' : undefined}
	{onBack}
>
	{#snippet chips()}<StateMark root={r} always />{/snippet}
	{#snippet subtitle()}<span class="min-w-0 max-w-full"><PathText text={r.path} /></span>{/snippet}
	{#snippet actions()}
		<Tooltip text="Folder settings" position="bottom"><IconButton icon="sliders" label="Folder settings" active={sel?.kind === 'root'} onclick={() => (sel = sel?.kind === 'root' ? null : { kind: 'root' })} /></Tooltip>
		<Tooltip text="Check now" position="bottom"><IconButton icon="refresh" label="Check {r.label} now" loading={ctrl.isBusy(`probe:${r.id}`)} onclick={() => ctrl.probe(r)} /></Tooltip>
		<Tooltip text={r.kind === 'home' ? 'The built-in folder cannot be re-detected' : 'Detect again'} position="bottom">
			<IconButton icon="scan-search" label="Detect again" disabled={r.kind === 'home' || r.state !== 'online'} loading={ctrl.isBusy(`detect:${r.id}`)} onclick={() => ctrl.detectAgain(r)} />
		</Tooltip>
	{/snippet}
</DetailHeader>
<div class="flex flex-shrink-0 flex-wrap items-center gap-x-5 gap-y-1 border-b border-line bg-surface-1 px-4 py-2 font-mono text-[12px] tabular-nums text-fg-muted sm:px-6" data-root-stats>
	<span>{formatCount(rootFiles(r))} files</span><span>{formatBytes(rootBytes(r), 1)}</span>
	{#if rootUnindexed(r) > 0}<span>{formatCount(rootUnindexed(r))} not indexed</span>{/if}
	<span>checked {agoText(r.state_checked_at)}</span>
</div>
<DetailBody>
	{#if r.state !== 'online'}
		<div class="mb-4">
			<Alert variant={r.state === 'offline' ? 'danger' : 'warning'} icon title="{r.label} is {r.state}">
				{r.state_reason}
				{#snippet actions()}
					<Button variant="secondary" size="sm" loading={ctrl.isBusy(`probe:${r.id}`)} onclick={() => ctrl.probe(r)}>Check again</Button>
				{/snippet}
			</Alert>
		</div>
	{/if}
	{#if ctrl.again[r.id]}<div class="mb-4"><AgainPanel {ctrl} root={r} /></div>{/if}
	<div class="flex items-start gap-4">
		<div class="min-w-0 flex-1">
			<section class="overflow-hidden rounded-lg border border-line bg-surface-1 shadow-raised" aria-label="Type folders">
				<div class="{COLS} hidden border-b border-line bg-surface-2 py-2 pl-4 pr-3 font-mono text-[12px] uppercase tracking-[0.06em] text-fg-subtle md:grid">
					<span>Type</span><span>Folder</span><span class="text-right">Files</span><span class="text-right">Size</span><span>Status</span><span></span>
				</div>
				{#if r.bindings.length === 0}
					<p class="px-4 py-8 text-center text-sm text-fg-subtle">No types bound to this folder yet. Open Folder settings to add one.</p>
				{/if}
				{#each r.bindings as b (bindingKey(r.id, b))}
					<div
						class="group {COLS} cursor-pointer border-t border-line py-1.5 pl-4 pr-3 transition-colors first:border-t-0 hover:bg-surface-2 {isSelB(b) ? 'bg-signal/10 shadow-[inset_2px_0_0_rgb(var(--signal))]' : ''}"
						role="button"
						tabindex="0"
						aria-pressed={isSelB(b)}
						data-binding-row
						onclick={() => selectBinding(b)}
						onkeydown={(e) => key(e, () => selectBinding(b))}
					>
						<div class="min-w-0">
							<span class="block truncate text-sm text-fg">{typeLabel(b.model_type)}</span>
							<div class="md:hidden"><PathText text={b.subdir} full={b.path} /></div>
							<div class="mt-1 flex flex-wrap items-center gap-1 md:hidden">{@render statusBadges(b)}</div>
						</div>
						<div class="hidden min-w-0 md:block"><PathText text={b.subdir} full={b.path} /></div>
						<span class="hidden text-right font-mono text-[12px] tabular-nums text-fg-muted md:block" data-cell="files">{formatCount(bindingFiles(b))}</span>
						<span class="hidden text-right font-mono text-[12px] tabular-nums text-fg-muted md:block">{formatBytes(b.size_bytes, 1)}</span>
						<div class="hidden flex-wrap items-center gap-1 md:flex">{@render statusBadges(b)}</div>
						<span class="whitespace-nowrap text-right font-mono text-[12px] tabular-nums text-fg-muted md:hidden">{formatCount(bindingFiles(b))} - {formatBytes(b.size_bytes, 1)}</span>
						<div role="presentation" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>
							<Menu label="Actions for {b.subdir || typeLabel(b.model_type)}" tooltip="Actions" items={rowMenu(b)} onselect={(id) => onRow(b, id)} />
						</div>
					</div>
				{/each}
			</section>
		</div>
		{#if sel && (sel.kind === 'root' || selBinding)}
			<button type="button" class="fixed inset-0 z-30 lg:hidden" style="background: rgb(var(--scrim) / 0.5)" aria-label="Close details" onclick={() => (sel = null)}></button>
			<aside
				class="z-40 flex flex-col border-line bg-surface-1 shadow-overlay max-lg:fixed max-lg:inset-x-0 max-lg:bottom-0 max-lg:max-h-[88dvh] max-lg:rounded-t-xl max-lg:border-t lg:sticky lg:top-0 lg:max-h-[calc(100dvh-10rem)] lg:w-[340px] lg:flex-shrink-0 lg:rounded-lg lg:border"
				aria-label="Details"
				data-side-panel
			>
				<div class="flex min-h-12 items-center gap-2 border-b border-line px-4 py-2">
					<Icon name={sel.kind === 'root' ? 'sliders' : 'folder-open'} className="h-4 w-4 flex-shrink-0 text-fg-muted" />
					<div class="min-w-0 flex-1">
						<div class="truncate text-sm font-semibold text-fg">{sel.kind === 'root' ? 'Folder settings' : typeLabel(sel.type)}</div>
						<div class="truncate text-[12px] text-fg-subtle">{r.label}</div>
					</div>
					<Tooltip text="Close" position="bottom"><IconButton icon="close" label="Close details" size="sm" onclick={() => (sel = null)} /></Tooltip>
				</div>
				<div class="min-h-0 flex-1 overflow-y-auto p-4">
					{#if sel.kind === 'root'}
						<RootPanel {ctrl} root={r} onDeleted={() => (sel = null)} />
					{:else if selBinding}
						<BindingPanel {ctrl} root={r} binding={selBinding} onRemoved={() => (sel = { kind: 'root' })} />
					{/if}
				</div>
			</aside>
		{/if}
	</div>
</DetailBody>
