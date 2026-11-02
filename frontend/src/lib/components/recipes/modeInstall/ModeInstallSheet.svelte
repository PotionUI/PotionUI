<script lang="ts">
	import GatedModelLink from '$lib/components/models/GatedModelLink.svelte';
	import { untrack } from 'svelte';
	import { Alert, Badge } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import ProviderKeyDisclosure from '../ProviderKeyDisclosure.svelte';
	import { formatBytes, formatBytesShort } from '$lib/utils/format';
	import type { ModeConsentRequest, ModesGrantBody } from '$lib/services/api/recipeModes';
	import ModeSlotCustomizer from './ModeSlotCustomizer.svelte';
	import {
		buildGrantBody,
		computeTotals,
		diskShortfall,
		estimateSeconds,
		formatEta,
		formatSpeed,
		freeHere,
		gatedNotice,
		initialInstallState,
		installBlocker,
		installLabel,
		joinLabels,
		mergeReplan,
		selectedSlotIds,
		sizeLabel,
		type ModeInstallState
	} from './modeInstallModel';

	let {
		request,
		preselect = [],
		busy = false,
		error = '',
		onConfirm,
		onCancel,
		onReplan
	}: {
		request: ModeConsentRequest;
		preselect?: string[];
		busy?: boolean;
		error?: string;
		onConfirm: (body: ModesGrantBody) => unknown;
		onCancel: () => void;
		onReplan?: (target: string, modes: string[]) => Promise<ModeConsentRequest | null>;
	} = $props();

	let current = $state.raw<ModeConsentRequest>(untrack(() => request));
	let choice = $state<ModeInstallState>(untrack(() => initialInstallState(request, preselect)));
	let customizeOpen = $state(false);
	let tokenOpen = $state(false);
	let replanning = $state(false);
	let replanError = $state('');

	const totals = $derived(computeTotals(current, choice));
	const selectedIds = $derived(selectedSlotIds(current, choice));
	const shortfall = $derived(diskShortfall(current, totals));
	const blocker = $derived(installBlocker(current, choice));
	const seconds = $derived(estimateSeconds(current, totals.bytes));
	const gated = $derived(gatedNotice(current, choice));
	const free = $derived(freeHere(current));
	const remote = $derived(current.target.kind === 'remote');
	const external = $derived(current.target.kind === 'external');
	const options = $derived(current.target.options ?? []);
	const readyCount = $derived(current.modes.filter((mode) => mode.ready).length);
	const missingModes = $derived(current.modes.filter((mode) => !mode.ready && !mode.locked));
	
	const gpuShort = $derived(
		current.gpu?.name
			? `${current.gpu.name.replace(/^NVIDIA GeForce /, '')}${current.gpu.vram_gb ? ` · ${Math.round(current.gpu.vram_gb)} GB` : ''}`
			: ''
	);
	const targetGpu = $derived.by(() => {
		const name = current.target.gpu_name;
		if (!name) return gpuShort;
		const short = name.replace(/^NVIDIA GeForce /, '');
		return `${short}${current.target.vram_gb ? ` · ${Math.round(current.target.vram_gb)} GB` : ''}${remote ? ' on the worker' : ''}`;
	});
	const filesCount = $derived(
		current.slots.filter((slot) => !slot.extra && !slot.manual && selectedIds.has(slot.id)).length
	);
	const summaryLine = $derived(gpuShort ? `Balanced for your ${gpuShort}` : 'Recommended files');
	const freeLabel = $derived(remote ? 'Free on worker' : 'Free here');
	const confirmLabel = $derived(installLabel(current, choice));

	function toggleMode(id: string) {
		const mode = current.modes.find((entry) => entry.id === id);
		if (!mode || mode.ready || mode.locked || busy) return;
		choice.modes[id] = !choice.modes[id];
	}

	function selectAllMissing() {
		for (const mode of missingModes) choice.modes[mode.id] = true;
	}

	function pickVariant(slotId: string, variantId: string) {
		choice.variants = { ...choice.variants, [slotId]: variantId };
	}

	function toggleExtra(slotId: string, ticked: boolean) {
		choice.extras = { ...choice.extras, [slotId]: ticked };
	}

	async function chooseTarget(backendId: string) {
		if (backendId === choice.targetId || replanning || busy) return;
		if (!onReplan) {
			choice.targetId = backendId;
			return;
		}
		replanning = true;
		replanError = '';
		try {
			const ticked = current.modes.filter((mode) => choice.modes[mode.id] && !mode.ready).map((mode) => mode.id);
			const next = await onReplan(backendId, ticked);
			if (next) {
				current = mergeReplan(current, next);
				choice.targetId = backendId;
				choice.variants = {};
				const kept: Record<string, boolean> = {};
				for (const mode of current.modes) {
					kept[mode.id] = mode.ready || mode.locked || !!choice.modes[mode.id];
				}
				choice.modes = kept;
			} else {
				replanError = "Couldn't check that computer.";
			}
		} catch {
			replanError = "Couldn't check that computer.";
		} finally {
			replanning = false;
		}
	}

	function confirm() {
		if (blocker || busy) return false;
		return onConfirm(buildGrantBody(current, choice));
	}

	function targetFreeLabel(free_bytes: number | null): string {
		return free_bytes == null ? 'checking space' : `${formatBytesShort(free_bytes)} free`;
	}
</script>

{#snippet footerNote()}
	{#if error}
		<span class="flex items-center gap-1.5 text-sm text-danger" data-mode-install-error>
			<Icon name="warning" className="h-3.5 w-3.5 shrink-0" />
			{error}
		</span>
	{:else if blocker}
		<span class="flex items-center gap-1.5 text-sm {shortfall ? 'text-danger' : 'text-fg-muted'}" data-mode-blocker>
			<Icon name="warning" className="h-3.5 w-3.5 shrink-0" />
			{blocker}
		</span>
	{/if}
{/snippet}

<ConfirmDialog
	isOpen={true}
	title="Install {current.preset_name} modes"
	subtitle="Pick the modes you want. Files you already have are kept."
		sizeClass="md:max-w-3xl md:w-full mode-install-dialog"
	compact
		stackOnPhone
	splitOnPhone
	subtitleWrap
	{busy}
	{confirmLabel}
	confirmDisabled={!!blocker || replanning}
	leftActions={footerNote}
	onConfirm={confirm}
	{onCancel}
>
	<div data-mode-install-sheet class="flex min-h-full flex-col">
		{#if options.length > 1}
			<div class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3" data-mode-target>
				<span class="font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">Install on</span>
				<div class="flex gap-1 rounded-lg border border-line bg-surface-2 p-0.5" role="radiogroup" aria-label="Install on">
					{#each options as option (option.backend_id)}
						{@const active = option.backend_id === choice.targetId}
						{@const tight = active && !!shortfall}
						<button
							type="button"
							role="radio"
							aria-checked={active}
							data-target={option.backend_id}
							disabled={replanning || busy}
							class="flex min-w-0 flex-col items-start rounded border px-3 py-1 text-left transition-colors max-sm:flex-1 max-sm:basis-0 {active
								? 'border-signal bg-signal/10'
								: 'border-transparent hover:bg-surface-3'}"
							onclick={() => chooseTarget(option.backend_id)}
						>
							<span class="flex min-w-0 max-w-full items-center gap-1.5 text-sm font-medium text-fg">
								<Icon name={option.kind === 'remote' ? 'server' : 'monitor'} className="h-3.5 w-3.5 flex-shrink-0 text-fg-muted" />
								<span class="truncate">{option.label}</span>
							</span>
							<span class="font-mono text-sm tabular-nums {tight ? 'text-danger' : 'text-fg-muted'}">
								{targetFreeLabel(option.free_bytes)}
							</span>
						</button>
					{/each}
				</div>
				{#if targetGpu}
					<span class="text-sm text-fg-muted max-sm:basis-full">{targetGpu}</span>
				{/if}
			</div>
		{/if}

		{#if replanError}
			<div class="px-5 pt-3"><Alert variant="danger" density="compact">{replanError}</Alert></div>
		{/if}

		{#if shortfall}
			<div class="px-5 pt-4" data-mode-disk-short>
				<Alert variant="danger" icon="warning">
					<p class="font-semibold">Not enough space on {current.target.label}</p>
					<p class="text-fg-muted">
						You picked {formatBytes(shortfall.needed, 1)}, but only {formatBytes(shortfall.usable, 1)} can be used:
						{formatBytes(shortfall.disk.free_bytes ?? 0, 1)} is free and {formatBytes(shortfall.disk.reserve_bytes, 1)} is kept spare.
						Deselect about {formatBytes(shortfall.short, 1)}{remote ? ', or install on this computer instead.' : '.'}
					</p>
					{#if remote}
						{@const local = options.find((option) => option.kind === 'local')}
						{#if local}
							<button
								type="button"
								class="mt-2 rounded border border-line-strong bg-surface-2 px-2.5 py-1 text-sm text-fg hover:bg-surface-3"
								data-install-local
								onclick={() => chooseTarget(local.backend_id)}
							>
								Install on this computer
							</button>
						{/if}
					{/if}
				</Alert>
			</div>
		{/if}

		{#if external}
			<div class="px-5 pt-4">
				<Alert variant="info" density="compact">
					This server manages its own files. Add the files you need there.
				</Alert>
			</div>
		{/if}

		<div class="px-5 pb-1 pt-4">
			<div class="flex items-center justify-between">
				<span class="font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">
					Modes {readyCount} of {current.modes.length} ready
				</span>
				{#if missingModes.length > 0}
					<button
						type="button"
						class="text-base text-signal hover:underline"
						data-select-all-missing
						onclick={selectAllMissing}
					>
						Select all missing
					</button>
				{/if}
			</div>
		</div>
		<ul class="border-b border-line" data-mode-list>
			{#each current.modes as mode (mode.id)}
				{@const locked = mode.ready || mode.locked}
				{@const ticked = !!choice.modes[mode.id]}
				<li class="border-t border-line {ticked && !locked ? 'bg-surface-2/60' : ''}">
					<label
						class="flex items-center gap-3 px-5 py-1.5 {locked ? 'cursor-default' : 'cursor-pointer hover:bg-surface-2'}"
						data-mode-row={mode.id}
					>
						<input
							type="checkbox"
							class="h-4 w-4 shrink-0 accent-signal-solid"
							checked={ticked}
							disabled={locked || busy}
							aria-label={mode.label}
							onchange={() => toggleMode(mode.id)}
						/>
						<span class="flex min-w-0 flex-1 gap-x-2 max-sm:flex-col sm:flex-wrap sm:items-baseline">
							<span class="text-base font-medium text-fg">{mode.label}</span>
							{#if mode.short_description}
								<span class="min-w-0 truncate text-sm text-fg-muted">{mode.short_description}</span>
							{/if}
						</span>
						{#if mode.ready}
							<Badge variant="success" size="sm">Installed</Badge>
						{:else if mode.locked}
							<span class="font-mono text-sm text-fg-muted">Included</span>
						{:else}
							<span class="shrink-0 font-mono text-sm tabular-nums text-fg-muted">
								+ {sizeLabel(mode.missing_bytes)}
							</span>
						{/if}
					</label>
				</li>
			{/each}
		</ul>

		{#if gated}
			<div class="px-5 pt-4" data-mode-gated>
				<Alert variant="warning" icon="warning" density="compact">
					<p class="text-fg">
						{joinLabels(gated.labels)} {gated.labels.length === 1 ? 'file is' : 'files are'} gated on Hugging Face.
						Accept the licence there, then add your token.
					</p>
					<div class="mt-1 flex gap-3 text-sm">
						{#if gated.licenseUrl}
							<GatedModelLink source={{ license_url: gated.licenseUrl }} class="text-sm" />
						{/if}
						{#if current.providers.length > 0}
							<button type="button" class="text-signal hover:underline" onclick={() => (tokenOpen = !tokenOpen)}>
								Add token
							</button>
						{:else}
							<a href="/admin?tab=plugins" class="text-signal hover:underline">Add token</a>
						{/if}
					</div>
					{#if tokenOpen}
						<div class="mt-2 space-y-2">
							{#each current.providers as provider (provider.id)}
								<ProviderKeyDisclosure {provider} />
							{/each}
						</div>
					{/if}
				</Alert>
			</div>
		{:else if current.warnings.length > 0}
			<div class="px-5 pt-4">
				<Alert variant="warning" density="compact">
					{#each current.warnings as warning (warning)}<p>{warning}</p>{/each}
				</Alert>
			</div>
		{/if}

		<div class="mt-4 border-t border-line">
			<button
				type="button"
				class="flex w-full items-center gap-2 px-5 py-3 text-left hover:bg-surface-2"
				aria-expanded={customizeOpen}
				data-customize-toggle
				onclick={() => (customizeOpen = !customizeOpen)}
			>
				<Icon name={customizeOpen ? 'chevron-down' : 'chevron-right'} className="h-3.5 w-3.5 text-fg-subtle" />
				<span class="text-base font-semibold text-fg">Customize per slot</span>
				{#if !customizeOpen}
					<span class="min-w-0 truncate text-sm text-fg-muted">{summaryLine}</span>
					<span class="ml-auto shrink-0 font-mono text-sm tabular-nums text-fg-subtle">
						{filesCount} {filesCount === 1 ? 'file' : 'files'}
					</span>
				{/if}
			</button>
			{#if customizeOpen}
				<ModeSlotCustomizer request={current} state={choice} onPick={pickVariant} onToggleExtra={toggleExtra} />
			{/if}
		</div>

		<div
			class="sticky bottom-0 mt-auto grid border-t border-line bg-surface-2 {current.estimate ? 'grid-cols-3' : 'grid-cols-2'}"
			data-mode-totals
		>
			<div class="px-5 py-3 max-sm:px-3">
				<div class="font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">To download</div>
				<div class="font-mono text-base tabular-nums sm:text-lg {shortfall ? 'text-danger' : 'text-fg'}" data-total-download>
					{sizeLabel(totals.bytes)}
				</div>
			</div>
			<div class="border-l border-line px-5 py-3 max-sm:px-3">
				<div class="font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">{freeLabel}</div>
				<div class="font-mono text-base tabular-nums sm:text-lg {shortfall ? 'text-danger' : 'text-fg'}" data-total-free>
					{free == null ? '—' : sizeLabel(free)}
				</div>
			</div>
			{#if current.estimate}
				<div class="border-l border-line px-5 py-3 max-sm:px-3" data-total-time>
					<div class="font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">Time</div>
					<div class="font-mono text-base tabular-nums text-fg sm:text-lg">
						{shortfall || seconds == null ? '—' : formatEta(seconds)}
					</div>
					{#if !shortfall && seconds != null}
						<div class="text-sm text-fg-muted max-sm:hidden">at your recent speed, {formatSpeed(current.estimate.bytes_per_sec)}</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>

</ConfirmDialog>

<style>
	@media (max-width: 639px) {
		:global(.mode-install-dialog kbd) {
			display: none;
		}
	}
</style>
