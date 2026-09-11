<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import MediaPreview from '$lib/components/MediaPreview.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { IconButton, SegmentedControl, Spinner } from '$lib/components/ui';
	import CompareFrame from './CompareFrame.svelte';
	import { COMPARE_DEFAULT, type CompareMode } from './compareFrame';
	import portal from '$lib/actions/portal';
	import type { GenerationHistoryItem, GenerationFile } from '$lib/types/history';
	import type { GenerationParamModel } from '$lib/types/generation';
	import { isImageFileType, isVideoFileType } from '$lib/utils/fileType';
	import type { HistoryToolContext } from '$lib/history/tools';

	// Mounted by the History Tools host as the `compare` tool, which only
	// offers it when exactly two generations are selected.
	export let context: HistoryToolContext;
	export let onClose: () => void;
	export let onDone: () => void;

	const MODE_ITEMS = [
		{ id: 'side-by-side', label: 'Side by side' },
		{ id: 'overlay', label: 'Overlay' },
		{ id: 'wipe', label: 'Wipe' }
	];

	let mode: CompareMode = 'side-by-side';
	let compareValue = COMPARE_DEFAULT;
	let viewerOpen = false;
	let viewerTarget: 'left' | 'right' | 'composed' = 'composed';

	$: left = context.generations[0];
	$: right = context.generations[1];

	interface ParamsResult {
		parameters: Record<string, unknown>;
		models: GenerationParamModel[];
	}

	let loading = true;
	let error: string | null = null;
	let leftParams: ParamsResult | null = null;
	let rightParams: ParamsResult | null = null;

	// Prefer the first final image/video; fall back to the first file.
	function previewFile(gen: GenerationHistoryItem): GenerationFile | null {
		const isMedia = (f: GenerationFile) => isImageFileType(f.file_type) || isVideoFileType(f.file_type);
		return gen.files.find((f) => f.is_final && isMedia(f)) ?? gen.files.find(isMedia) ?? gen.files[0] ?? null;
	}

	$: leftFile = previewFile(left);
	$: rightFile = previewFile(right);
	$: bothImages = !!leftFile && !!rightFile && isImageFileType(leftFile.file_type) && isImageFileType(rightFile.file_type);
	// Overlay/wipe only make sense for two images; fall back the moment either
	// side isn't one (e.g. the tool host later allows a video generation).
	$: if (!bothImages && mode !== 'side-by-side') mode = 'side-by-side';

	function openViewer(target: 'left' | 'right' | 'composed') {
		viewerTarget = target;
		viewerOpen = true;
	}

	function handleWindowKeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape') return;
		e.preventDefault();
		if (viewerOpen) {
			viewerOpen = false;
		} else {
			onClose();
		}
	}

	function formatValue(value: unknown): string {
		if (value === null || value === undefined || value === '') return '—';
		if (typeof value === 'object') {
			try {
				return JSON.stringify(value);
			} catch {
				return String(value);
			}
		}
		return String(value);
	}

	function modelsSummary(models: GenerationParamModel[] | undefined): string {
		if (!models || models.length === 0) return '—';
		return models
			.map((m) => {
				const name = (m as any).filename ?? m.name ?? (m as any).type ?? 'model';
				return m.weight !== undefined && m.weight !== null ? `${name} (${m.weight})` : `${name}`;
			})
			.join(', ');
	}

	// Ordered, human-friendly keys surfaced first when present.
	const PRIORITY_KEYS = ['seed', 'steps', 'cfg', 'cfg_scale', 'true_cfg_scale', 'sampler', 'scheduler', 'model'];

	interface DiffRow {
		key: string;
		leftValue: string;
		rightValue: string;
		changed: boolean;
	}

	$: rows = buildRows(leftParams, rightParams);

	function buildRows(l: ParamsResult | null, r: ParamsResult | null): DiffRow[] {
		if (!l || !r) return [];
		const lp = l.parameters ?? {};
		const rp = r.parameters ?? {};
		const keys = new Set<string>([...Object.keys(lp), ...Object.keys(rp)]);

		const ordered: string[] = [];
		for (const k of PRIORITY_KEYS) {
			if (keys.has(k)) {
				ordered.push(k);
				keys.delete(k);
			}
		}
		ordered.push(...Array.from(keys).sort());

		const rowsOut: DiffRow[] = ordered.map((key) => {
			const leftValue = formatValue(lp[key]);
			const rightValue = formatValue(rp[key]);
			return { key, leftValue, rightValue, changed: leftValue !== rightValue };
		});

		// Models comparison row (appended at the end)
		const leftModels = modelsSummary(l.models);
		const rightModels = modelsSummary(r.models);
		rowsOut.push({
			key: 'models',
			leftValue: leftModels,
			rightValue: rightModels,
			changed: leftModels !== rightModels
		});

		return rowsOut;
	}

	onMount(async () => {
		try {
			const [lRes, rRes] = await Promise.all([
				api.getGenerationParams(left.id, 0),
				api.getGenerationParams(right.id, 0)
			]);
			if (lRes.success && lRes.data) leftParams = lRes.data as ParamsResult;
			if (rRes.success && rRes.data) rightParams = rRes.data as ParamsResult;
			if (!leftParams || !rightParams) {
				error = 'Could not load parameters for one or both generations.';
			}
		} catch (e) {
			logger.error('Failed to load compare params:', getErrorMessage(e));
			error = 'Failed to load parameters.';
		} finally {
			loading = false;
		}
	});

</script>

<svelte:window on:keydown={handleWindowKeydown} />

<BaseModal isOpen={true} size="xl" handleEscapeKey={false} on:close={onClose}>
	<svelte:fragment slot="headerIcon">
		<Icon name="layers" className="w-4 h-4 text-signal" />
	</svelte:fragment>
	<svelte:fragment slot="header">
		<h3 class="text-sm font-semibold text-fg">Compare generations</h3>
		{#if bothImages}
			<SegmentedControl
				items={MODE_ITEMS}
				selected={mode}
				onSelect={(id) => (mode = id as CompareMode)}
				ariaLabel="Compare mode"
			/>
		{/if}
	</svelte:fragment>

	<!-- Image panes -->
	<div class="p-4">
		<CompareFrame
			{mode}
			{leftFile}
			{rightFile}
			leftGenerationId={left.id}
			rightGenerationId={right.id}
			value={compareValue}
			on:valuechange={(e) => (compareValue = e.detail)}
			on:expand={(e) => openViewer(e.detail)}
		/>
		<div class="grid grid-cols-2 gap-3 mt-2">
			<div class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle truncate text-center">
				A · {left.preset_name ?? left.id.slice(0, 8)}
			</div>
			<div class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle truncate text-center">
				B · {right.preset_name ?? right.id.slice(0, 8)}
			</div>
		</div>
	</div>

	<!-- Parameter diff -->
	<div class="px-4 pb-4">
		{#if loading}
			<div class="flex items-center justify-center gap-2 py-10 text-fg-muted">
				<Spinner size="sm" />
				<span class="text-sm">Loading parameters…</span>
			</div>
		{:else if error}
			<div class="py-8 text-center text-sm text-danger">{error}</div>
		{:else}
			<div class="overflow-x-auto rounded-lg border border-line">
				<table class="w-full text-sm">
					<thead>
						<tr class="bg-surface-2 text-fg-subtle">
							<th class="text-left font-medium px-3 py-2 w-1/4">Parameter</th>
							<th class="text-left font-medium px-3 py-2">A</th>
							<th class="text-left font-medium px-3 py-2">B</th>
						</tr>
					</thead>
					<tbody>
						{#each rows as row (row.key)}
							<tr
								class="border-t border-line {row.changed ? 'bg-signal/[0.06]' : ''}"
							>
								<td class="px-3 py-2 align-top text-fg-muted font-mono text-xs">{row.key}</td>
								<td
									class="px-3 py-2 align-top font-mono text-xs tabular-nums break-all {row.changed
										? 'text-signal'
										: 'text-fg-muted'}"
								>
									{row.leftValue}
								</td>
								<td
									class="px-3 py-2 align-top font-mono text-xs tabular-nums break-all {row.changed
										? 'text-signal'
										: 'text-fg-muted'}"
								>
									{row.rightValue}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="mt-2 text-2xs text-fg-subtle">
				Rows highlighted in <span class="text-signal">blue</span> differ between the two generations.
			</p>
		{/if}
	</div>
</BaseModal>

{#if viewerOpen}
	<!-- Full-size viewer. Portaled above BaseModal's own backdrop; the modal
	     stays mounted underneath so closing the viewer returns to it. -->
	<div
		use:portal
		class="fixed inset-0 z-[10000] bg-black flex items-center justify-center"
		role="button"
		tabindex="-1"
		aria-label="Close full size view"
		on:click={() => (viewerOpen = false)}
		on:keydown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); viewerOpen = false; } }}
	>
		<div class="absolute top-4 right-4">
			<IconButton icon="close" label="Close full size" variant="secondary" onclick={() => (viewerOpen = false)} />
		</div>
		<div class="w-full h-full p-6 md:p-10 flex items-center justify-center" on:click|stopPropagation on:keydown|stopPropagation role="presentation">
			{#if viewerTarget === 'composed'}
				<CompareFrame
					{mode}
					{leftFile}
					{rightFile}
					leftGenerationId={left.id}
					rightGenerationId={right.id}
					value={compareValue}
					boxClass="w-[min(92vw,1400px)] h-[min(76vh,1000px)]"
					showExpand={false}
					on:valuechange={(e) => (compareValue = e.detail)}
				/>
			{:else}
				{@const soloFile = viewerTarget === 'left' ? leftFile : rightFile}
				{#if soloFile}
					<MediaPreview
						file={soloFile}
						generationId={viewerTarget === 'left' ? left.id : right.id}
						thumbnailSize="large"
						loadFullOnClick={false}
						startFullLoaded
						fit="contain"
						className="w-full h-full"
					/>
				{/if}
			{/if}
		</div>
	</div>
{/if}
