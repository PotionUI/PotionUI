<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import MediaPreview from '$lib/components/MediaPreview.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import type { GenerationHistoryItem, GenerationFile } from '$lib/types/history';
	import type { GenerationParamModel } from '$lib/types/generation';
	import { isImageFileType, isVideoFileType } from '$lib/utils/fileType';

	export let left: GenerationHistoryItem;
	export let right: GenerationHistoryItem;
	export let onClose: () => void;
	/** Only mounted from inside a parent `{#if}` today; default keeps that call site unchanged. */
	export let isOpen: boolean = true;

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

<BaseModal {isOpen} size="xl" on:close={onClose}>
	<svelte:fragment slot="headerIcon">
		<Icon name="layers" className="w-4 h-4 text-signal" />
	</svelte:fragment>
	<svelte:fragment slot="header">
		<h3 class="text-sm font-semibold text-fg">Compare generations</h3>
	</svelte:fragment>

	<!-- Image panes -->
	<div class="grid grid-cols-2 gap-3 p-4">
		<div class="flex flex-col gap-2">
			<div class="rounded-lg overflow-hidden bg-surface-2 aspect-square flex items-center justify-center">
				{#if leftFile}
					<MediaPreview file={leftFile} generationId={left.id} thumbnailSize="large" className="w-full h-full" />
				{:else}
					<span class="text-xs text-fg-subtle">No preview</span>
				{/if}
			</div>
			<div class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle truncate">
				A · {left.preset_name ?? left.id.slice(0, 8)}
			</div>
		</div>
		<div class="flex flex-col gap-2">
			<div class="rounded-lg overflow-hidden bg-surface-2 aspect-square flex items-center justify-center">
				{#if rightFile}
					<MediaPreview file={rightFile} generationId={right.id} thumbnailSize="large" className="w-full h-full" />
				{:else}
					<span class="text-xs text-fg-subtle">No preview</span>
				{/if}
			</div>
			<div class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle truncate">
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
